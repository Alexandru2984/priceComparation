import logging

from django.db import transaction

from comparator.models import Invoice, InvoiceLine, MetroOffer, ProductCode, SupplierOffer

from .matching import apply_match
from .ocr import extract_text, merge_ocr_pages
from .parser import parse_invoice_text


logger = logging.getLogger(__name__)


def metro_offer_source_for_invoice(invoice):
    source_number = invoice.number or str(invoice.pk)
    return f"{invoice.get_document_type_display()} METRO {source_number}"[:120]


def delete_derived_metro_offers(invoice):
    if not invoice.supplier.is_metro:
        return 0
    return MetroOffer.objects.filter(
        product_id__in=invoice.lines.exclude(matched_product_id=None).values("matched_product_id"),
        valid_from=invoice.issued_at,
        source=metro_offer_source_for_invoice(invoice),
    ).delete()[0]


def reconcile_derived_metro_offer(invoice, product_id):
    if not invoice.supplier.is_metro or not product_id:
        return
    replacement = invoice.lines.select_related("invoice", "invoice__supplier").filter(
        matched_product_id=product_id,
        needs_review=False,
    ).first()
    if replacement:
        sync_metro_offer_from_line(replacement)
        return
    MetroOffer.objects.filter(
        product_id=product_id,
        valid_from=invoice.issued_at,
        source=metro_offer_source_for_invoice(invoice),
    ).delete()


@transaction.atomic
def process_invoice(invoice, force_ocr=False):
    invoice.processing_error = ""
    if force_ocr or not invoice.ocr_text.strip():
        sources = []
        if invoice.document:
            sources.append((None, invoice.document.path))
        sources.extend((page, page.file.path) for page in invoice.pages.all())
        if not sources:
            raise ValueError("Documentul nu conține nici imagini/PDF, nici text pentru procesare.")
        chunks = []
        for page, path in sources:
            text = extract_text(path)
            chunks.append(text)
            if page:
                page.ocr_text = text
                page.save(update_fields=["ocr_text"])
        invoice.ocr_text = merge_ocr_pages(chunks)

    products, parser_name, parser_warning = parse_invoice_text(invoice.ocr_text)
    if not products:
        details = f" Ollama: {parser_warning}" if parser_warning else ""
        raise ValueError(f"Nu s-au găsit linii de produse. Poți introduce liniile manual.{details}")

    delete_derived_metro_offers(invoice)
    invoice.lines.all().delete()
    for data in products:
        line = InvoiceLine(invoice=invoice, **data)
        apply_match(line)
        # Cantitățile și ambalarea extrase din OCR trebuie confirmate cel puțin
        # o dată. Editarea liniei memorează apoi asocierea furnizorului.
        line.needs_review = True
        line.save()

    needs_review = invoice.lines.filter(needs_review=True).exists()
    invoice.status = Invoice.Status.REVIEW if needs_review else Invoice.Status.PROCESSED
    if parser_name == "heuristic" and parser_warning:
        invoice.processing_error = f"Ollama indisponibil; s-a folosit parserul simplu. {parser_warning}"
    invoice.save(update_fields=["ocr_text", "status", "processing_error"])
    return invoice


def sync_metro_offer_from_line(line):
    if not line.invoice.supplier.is_metro or line.needs_review or not line.matched_product_id:
        return None
    source = metro_offer_source_for_invoice(line.invoice)
    offer, _ = MetroOffer.objects.update_or_create(
        product=line.matched_product,
        valid_from=line.invoice.issued_at,
        source=source,
        defaults={
            "units_per_package": line.units_per_package,
            "unit_size": line.unit_size,
            "price_gross": line.unit_price_gross,
            "active": True,
        },
    )
    return offer


@transaction.atomic
def delete_invoice_line(line):
    invoice = line.invoice
    product_id = line.matched_product_id
    was_metro = invoice.supplier.is_metro
    line.delete()
    if was_metro and product_id:
        reconcile_derived_metro_offer(invoice, product_id)
    if invoice.lines.filter(needs_review=True).exists():
        invoice.status = Invoice.Status.REVIEW
    elif invoice.lines.exists():
        invoice.status = Invoice.Status.PROCESSED
    else:
        invoice.status = Invoice.Status.NEW
    invoice.save(update_fields=["status"])


def delete_invoice(invoice):
    stored_files = []
    if invoice.document and invoice.document.name:
        stored_files.append((invoice.document.storage, invoice.document.name))
    stored_files.extend(
        (page.file.storage, page.file.name)
        for page in invoice.pages.all()
        if page.file and page.file.name
    )

    def remove_stored_files():
        for storage, name in stored_files:
            try:
                storage.delete(name)
            except Exception:
                logger.exception("Could not remove stored invoice file %s", name)

    with transaction.atomic():
        delete_derived_metro_offers(invoice)
        invoice.delete()
        transaction.on_commit(remove_stored_files)


def sync_supplier_offer_from_line(line):
    if line.needs_review or not line.matched_product_id:
        SupplierOffer.objects.filter(invoice_line=line).delete()
        return None
    offer, _ = SupplierOffer.objects.update_or_create(
        invoice_line=line,
        defaults={
            "supplier": line.invoice.supplier,
            "product": line.matched_product,
            "price_per_base_unit": line.price_per_base_unit,
            "base_unit": line.base_unit,
            "valid_from": line.invoice.issued_at,
        },
    )
    if line.ean:
        ProductCode.objects.update_or_create(
            supplier=line.invoice.supplier,
            kind=ProductCode.Kind.SUPPLIER,
            code=line.ean.strip().upper(),
            defaults={"product": line.matched_product},
        )
    return offer


def sync_all_confirmed_metro_lines():
    synced = 0
    lines = InvoiceLine.objects.select_related("invoice", "invoice__supplier", "matched_product").filter(
        invoice__supplier__is_metro=True,
        needs_review=False,
        matched_product__isnull=False,
    )
    for line in lines:
        if sync_metro_offer_from_line(line):
            synced += 1
    return synced

import logging
from decimal import Decimal

from django.db import transaction

from comparator.models import (
    BaseUnit,
    Invoice,
    InvoiceLine,
    InvoiceRevision,
    MetroOffer,
    Product,
    ProductCode,
    SupplierOffer,
)

from .matching import apply_match
from .ocr import extract_text_result, merge_ocr_pages
from .parser import parse_invoice_text
from .inventory import sync_stock_from_line


logger = logging.getLogger(__name__)


LINE_DECIMAL_FIELDS = (
    "quantity",
    "units_per_package",
    "unit_size",
    "unit_price_gross",
    "vat_rate",
    "discount_gross",
    "deposit_gross",
)


def _serialize_invoice_line(line):
    data = {
        "original_name": line.original_name,
        "ean": line.ean,
        "base_unit": line.base_unit,
        "line_total_gross": str(line.line_total_gross) if line.line_total_gross is not None else None,
        "matched_product_id": line.matched_product_id,
        "match_score": line.match_score,
        "match_gap": line.match_gap,
        "match_method": line.match_method,
        "match_candidates": line.match_candidates,
        "match_corrected": line.match_corrected,
        "needs_review": line.needs_review,
    }
    data.update({field: str(getattr(line, field)) for field in LINE_DECIMAL_FIELDS})
    return data


def create_invoice_revision(invoice, reason, created_by=None, include_empty=False):
    lines = list(invoice.lines.order_by("id"))
    if not lines and not include_empty:
        return None
    snapshot = {
        "version": 1,
        "invoice": {
            "ocr_text": invoice.ocr_text,
            "processing_error": invoice.processing_error,
            "status": invoice.status,
        },
        "lines": [_serialize_invoice_line(line) for line in lines],
    }
    return InvoiceRevision.objects.create(
        invoice=invoice,
        reason=reason,
        snapshot=snapshot,
        line_count=len(lines),
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )


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
def process_invoice(invoice, force_ocr=False, created_by=None):
    create_invoice_revision(invoice, InvoiceRevision.Reason.OCR_REPROCESS, created_by=created_by)
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
            result = extract_text_result(path)
            chunks.append(result.text)
            if page:
                page.ocr_text = result.text
                page.ocr_quality_score = result.quality_score
                page.ocr_strategy = result.strategy
                page.ocr_warnings = result.warnings
                page.save(update_fields=[
                    "ocr_text",
                    "ocr_quality_score",
                    "ocr_strategy",
                    "ocr_warnings",
                ])
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


@transaction.atomic
def restore_invoice_revision(revision, created_by=None):
    invoice = Invoice.objects.select_for_update().select_related("supplier").get(pk=revision.invoice_id)
    snapshot = revision.snapshot
    if snapshot.get("version") != 1 or not isinstance(snapshot.get("lines"), list):
        raise ValueError("Versiunea salvată nu are un format recunoscut.")
    create_invoice_revision(invoice, InvoiceRevision.Reason.RESTORE, created_by=created_by, include_empty=True)
    delete_derived_metro_offers(invoice)
    invoice.lines.all().delete()

    product_ids = {
        item.get("matched_product_id")
        for item in snapshot["lines"]
        if item.get("matched_product_id")
    }
    existing_product_ids = set(Product.objects.filter(pk__in=product_ids).values_list("pk", flat=True))
    restored_lines = []
    for item in snapshot["lines"]:
        base_unit = item.get("base_unit")
        if base_unit not in BaseUnit.values:
            raise ValueError("Versiunea conține o unitate de măsură invalidă.")
        product_id = item.get("matched_product_id")
        product_exists = not product_id or product_id in existing_product_ids
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name=str(item.get("original_name", ""))[:240],
            ean=str(item.get("ean", ""))[:80],
            base_unit=base_unit,
            line_total_gross=(
                Decimal(item["line_total_gross"])
                if item.get("line_total_gross") is not None
                else None
            ),
            matched_product_id=product_id if product_id in existing_product_ids else None,
            match_score=int(item.get("match_score", 0)) if product_exists else 0,
            match_gap=int(item.get("match_gap", 0)) if product_exists else 0,
            match_method=(
                item.get("match_method")
                if item.get("match_method") in InvoiceLine.MatchMethod.values
                else InvoiceLine.MatchMethod.NONE
            ),
            match_candidates=item.get("match_candidates", []) if product_exists else [],
            match_corrected=bool(item.get("match_corrected", False)) if product_exists else False,
            needs_review=bool(item.get("needs_review", True)) or not product_exists,
            **{field: Decimal(str(item[field])) for field in LINE_DECIMAL_FIELDS},
        )
        restored_lines.append(line)

    invoice_data = snapshot.get("invoice", {})
    invoice.ocr_text = str(invoice_data.get("ocr_text", ""))
    invoice.processing_error = str(invoice_data.get("processing_error", ""))
    status = invoice_data.get("status")
    invoice.status = status if status in Invoice.Status.values else Invoice.Status.REVIEW
    if any(line.needs_review for line in restored_lines):
        invoice.status = Invoice.Status.REVIEW
    invoice.save(update_fields=["ocr_text", "processing_error", "status"])

    for line in restored_lines:
        sync_metro_offer_from_line(line)
        sync_supplier_offer_from_line(line)
        sync_stock_from_line(line)
    return invoice


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

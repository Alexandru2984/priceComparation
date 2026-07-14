from django.db import transaction

from comparator.models import Invoice, InvoiceLine, MetroOffer

from .matching import apply_match
from .ocr import extract_text
from .parser import parse_invoice_text


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
        invoice.ocr_text = "\n\n--- URMĂTOAREA IMAGINE ---\n\n".join(chunks)

    products, parser_name, parser_warning = parse_invoice_text(invoice.ocr_text)
    if not products:
        details = f" Ollama: {parser_warning}" if parser_warning else ""
        raise ValueError(f"Nu s-au găsit linii de produse. Poți introduce liniile manual.{details}")

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
    source_number = line.invoice.number or str(line.invoice.pk)
    source = f"{line.invoice.get_document_type_display()} METRO {source_number}"[:120]
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

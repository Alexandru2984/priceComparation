import logging

from django.core.exceptions import ValidationError
from django.db import transaction

from comparator.models import DocumentPage, Invoice
from comparator.validators import MAX_DOCUMENT_PAGES, MAX_DOCUMENT_TOTAL_SIZE

logger = logging.getLogger(__name__)


def _file_size(field_file):
    if not field_file or not field_file.name:
        return 0
    try:
        return field_file.size
    except OSError as exc:
        raise ValidationError("Un fișier existent nu mai poate fi citit din stocare.") from exc


def _mark_pages_changed(invoice):
    invoice.processing_error = "Fișierele documentului s-au modificat. Reprocesează OCR pentru a actualiza liniile."
    update_fields = ["processing_error"]
    if invoice.lines.exists():
        invoice.status = Invoice.Status.REVIEW
        update_fields.append("status")
    invoice.save(update_fields=update_fields)


def _remove_file_safely(storage, name):
    try:
        storage.delete(name)
    except Exception:
        logger.exception("Could not remove stored document page %s", name)


def add_document_pages(invoice, uploads):
    created_files = []
    try:
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
            pages = list(invoice.pages.select_for_update().order_by("page_order", "id"))
            if len(pages) + len(uploads) > MAX_DOCUMENT_PAGES:
                raise ValidationError(f"Un document poate avea maximum {MAX_DOCUMENT_PAGES} fișiere.")
            existing_size = _file_size(invoice.document) + sum(_file_size(page.file) for page in pages)
            if existing_size + sum(upload.size for upload in uploads) > MAX_DOCUMENT_TOTAL_SIZE:
                raise ValidationError("Documentul poate avea maximum 50 MB în total.")
            next_order = max((page.page_order for page in pages), default=0) + 1
            for offset, upload in enumerate(uploads):
                page = DocumentPage.objects.create(
                    invoice=invoice,
                    file=upload,
                    page_order=next_order + offset,
                )
                created_files.append((page.file.storage, page.file.name))
            _mark_pages_changed(invoice)
    except Exception:
        for storage, name in created_files:
            _remove_file_safely(storage, name)
        raise
    return len(created_files)


def delete_document_page(page):
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=page.invoice_id)
        page = DocumentPage.objects.select_for_update().get(pk=page.pk, invoice=invoice)
        storage = page.file.storage
        name = page.file.name
        page.delete()
        for order, remaining in enumerate(
            invoice.pages.select_for_update().order_by("page_order", "id"), start=1
        ):
            if remaining.page_order != order:
                remaining.page_order = order
                remaining.save(update_fields=["page_order"])
        _mark_pages_changed(invoice)
        if name:
            transaction.on_commit(lambda: _remove_file_safely(storage, name))
    return invoice.pk


def move_document_page(page, direction):
    if direction not in {"up", "down"}:
        raise ValidationError("Direcția de mutare nu este validă.")
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().get(pk=page.invoice_id)
        page = DocumentPage.objects.get(pk=page.pk, invoice=invoice)
        pages = list(invoice.pages.select_for_update().order_by("page_order", "id"))
        index = next(position for position, candidate in enumerate(pages) if candidate.pk == page.pk)
        target_index = index - 1 if direction == "up" else index + 1
        if target_index < 0 or target_index >= len(pages):
            return invoice.pk, False
        current = pages[index]
        target = pages[target_index]
        current_order = current.page_order
        current.page_order = 0
        current.save(update_fields=["page_order"])
        target_order = target.page_order
        target.page_order = current_order
        target.save(update_fields=["page_order"])
        current.page_order = target_order
        current.save(update_fields=["page_order"])
        _mark_pages_changed(invoice)
    return invoice.pk, True

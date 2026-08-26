from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from comparator.models import DocumentProcessingJob, Invoice

from .invoices import process_invoice


ACTIVE_STATUSES = [DocumentProcessingJob.Status.PENDING, DocumentProcessingJob.Status.RUNNING]


@transaction.atomic
def enqueue_document(invoice, requested_by=None, force_ocr=False):
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    active = invoice.processing_jobs.filter(status__in=ACTIVE_STATUSES).first()
    if active:
        if force_ocr and not active.force_ocr and active.status == DocumentProcessingJob.Status.PENDING:
            active.force_ocr = True
            active.save(update_fields=["force_ocr"])
        return active, False
    job = DocumentProcessingJob.objects.create(
        invoice=invoice,
        requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
        force_ocr=force_ocr,
    )
    invoice.status = Invoice.Status.NEW
    invoice.processing_error = "Documentul a fost adăugat în coada locală de procesare."
    invoice.save(update_fields=["status", "processing_error"])
    return job, True


@transaction.atomic
def claim_next_job():
    job = (
        DocumentProcessingJob.objects.select_for_update(skip_locked=True)
        .select_related("invoice")
        .filter(status=DocumentProcessingJob.Status.PENDING)
        .order_by("created_at")
        .first()
    )
    if not job:
        return None
    job.status = DocumentProcessingJob.Status.RUNNING
    job.started_at = timezone.now()
    job.finished_at = None
    job.attempts += 1
    job.error = ""
    job.save(update_fields=["status", "started_at", "finished_at", "attempts", "error"])
    return job


def process_claimed_job(job):
    try:
        process_invoice(
            job.invoice,
            force_ocr=job.force_ocr,
            created_by=job.requested_by,
        )
    except Exception as exc:
        message = str(exc)[:4000]
        Invoice.objects.filter(pk=job.invoice_id).update(
            status=Invoice.Status.ERROR,
            processing_error=message,
        )
        DocumentProcessingJob.objects.filter(pk=job.pk).update(
            status=DocumentProcessingJob.Status.ERROR,
            error=message,
            finished_at=timezone.now(),
        )
        return False
    DocumentProcessingJob.objects.filter(pk=job.pk).update(
        status=DocumentProcessingJob.Status.COMPLETED,
        error="",
        finished_at=timezone.now(),
    )
    return True


def recover_stale_jobs(max_age_minutes=30):
    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    return DocumentProcessingJob.objects.filter(
        status=DocumentProcessingJob.Status.RUNNING,
        started_at__lt=cutoff,
    ).update(
        status=DocumentProcessingJob.Status.PENDING,
        error="Workerul anterior s-a oprit; procesarea va fi reluată.",
        started_at=None,
    )

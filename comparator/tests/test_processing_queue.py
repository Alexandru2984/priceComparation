from datetime import date

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from comparator.models import DocumentProcessingJob, Invoice, Supplier
from comparator.services.processing_queue import enqueue_document


class DocumentProcessingQueueTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="queue-admin",
            password="A-test-password-2026!",
            is_staff=True,
        )
        self.supplier = Supplier.objects.create(name="Furnizor coadă")

    @override_settings(OLLAMA_ENABLED=False)
    def test_worker_processes_pasted_text_from_database_queue(self):
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 26),
            ocr_text="Coca Cola 2L - 2 bucati x 7,90 RON",
        )
        job, created = enqueue_document(invoice, requested_by=self.staff)
        duplicate, duplicate_created = enqueue_document(invoice, requested_by=self.staff, force_ocr=False)

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate, job)
        call_command("process_document_queue", once=True, verbosity=0)

        job.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(job.status, DocumentProcessingJob.Status.COMPLETED)
        self.assertEqual(job.attempts, 1)
        self.assertEqual(invoice.lines.count(), 1)
        self.assertEqual(invoice.status, Invoice.Status.REVIEW)

    @override_settings(OLLAMA_ENABLED=False)
    def test_worker_records_failure_without_losing_document(self):
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 26),
            ocr_text="text fără produse",
        )
        job, _ = enqueue_document(invoice, requested_by=self.staff)

        call_command("process_document_queue", once=True, verbosity=0)

        job.refresh_from_db()
        invoice.refresh_from_db()
        self.assertEqual(job.status, DocumentProcessingJob.Status.ERROR)
        self.assertEqual(invoice.status, Invoice.Status.ERROR)
        self.assertIn("Nu s-au găsit", job.error)
        self.assertEqual(invoice.ocr_text, "text fără produse")

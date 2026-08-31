from datetime import date, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from comparator.models import (
    ActivityLog,
    DocumentProcessingJob,
    Invoice,
    InvoiceRevision,
    MetroScrapedProduct,
    MetroScrapeJob,
    MetroScrapeTerm,
    Supplier,
)


class CleanupCommandTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("cleanup-user")
        old = timezone.now() - timedelta(days=60)
        self.old_log = ActivityLog.objects.create(
            user=self.user,
            method="POST",
            path="/app/old/",
            status_code=200,
            outcome=ActivityLog.Outcome.SUCCESS,
        )
        ActivityLog.objects.filter(pk=self.old_log.pk).update(created_at=old)
        self.recent_log = ActivityLog.objects.create(
            user=self.user,
            method="POST",
            path="/app/recent/",
            status_code=200,
            outcome=ActivityLog.Outcome.SUCCESS,
        )
        Session.objects.create(
            session_key="expired-session",
            session_data="",
            expire_date=timezone.now() - timedelta(days=1),
        )
        self.job = MetroScrapeJob.objects.create(
            status=MetroScrapeJob.Status.COMPLETED,
            start_url="https://produse.metro.ro/shop",
        )
        MetroScrapeJob.objects.filter(pk=self.job.pk).update(created_at=old)
        MetroScrapeTerm.objects.create(job=self.job, term="vechi")
        MetroScrapedProduct.objects.create(
            job=self.job,
            external_id="OLD",
            name="Produs staging vechi",
            product_url="https://produse.metro.ro/shop/pv/OLD/test",
            price_gross=1,
        )
        supplier = Supplier.objects.create(name="Furnizor cleanup")
        self.invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 31))
        processing = DocumentProcessingJob.objects.create(
            invoice=self.invoice,
            status=DocumentProcessingJob.Status.COMPLETED,
        )
        DocumentProcessingJob.objects.filter(pk=processing.pk).update(created_at=old)
        for index in range(4):
            InvoiceRevision.objects.create(
                invoice=self.invoice,
                reason=InvoiceRevision.Reason.OCR_REPROCESS,
                snapshot={"index": index},
            )

    def test_dry_run_reports_without_deleting(self):
        output = StringIO()

        call_command(
            "cleanup_pricematch",
            activity_days=30,
            technical_days=30,
            revision_limit=2,
            stdout=output,
        )

        self.assertIn('"mode": "simulare"', output.getvalue())
        self.assertTrue(ActivityLog.objects.filter(pk=self.old_log.pk).exists())
        self.assertEqual(InvoiceRevision.objects.filter(invoice=self.invoice).count(), 4)

    def test_confirm_removes_only_expired_technical_data(self):
        call_command(
            "cleanup_pricematch",
            "--confirm",
            activity_days=30,
            technical_days=30,
            revision_limit=2,
            verbosity=0,
        )

        self.assertFalse(ActivityLog.objects.filter(pk=self.old_log.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(pk=self.recent_log.pk).exists())
        self.assertFalse(Session.objects.filter(session_key="expired-session").exists())
        self.assertTrue(MetroScrapeJob.objects.filter(pk=self.job.pk).exists())
        self.assertFalse(MetroScrapedProduct.objects.filter(job=self.job).exists())
        self.assertFalse(MetroScrapeTerm.objects.filter(job=self.job).exists())
        self.assertFalse(DocumentProcessingJob.objects.filter(invoice=self.invoice).exists())
        self.assertEqual(InvoiceRevision.objects.filter(invoice=self.invoice).count(), 2)
        self.assertTrue(Invoice.objects.filter(pk=self.invoice.pk).exists())

    def test_retention_cannot_be_set_to_dangerously_short_value(self):
        with self.assertRaisesMessage(CommandError, "cel puțin 30"):
            call_command("cleanup_pricematch", activity_days=1)

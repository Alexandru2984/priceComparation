from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django_otp.plugins.otp_totp.models import TOTPDevice

from comparator.models import ActivityLog, DocumentProcessingJob, Invoice, MetroScrapeJob, Supplier


class OperationsPageTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.admin = users.objects.create_superuser(
            "owner", email="owner-secret@example.test", password="Strong-test-password-2026!"
        )
        self.operator = users.objects.create_user("operator", password="Strong-test-password-2026!", is_staff=True)

    def test_operator_cannot_open_operations_center(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get("/app/operare/").status_code, 403)

    @override_settings(
        SECRET_KEY="never-render-this-secret",
        OLLAMA_URL="http://user:password@secret-host:11434",
        WEBPUSH_VAPID_PRIVATE_KEY="private-key-must-stay-hidden",
        PREFERRED_METRO_STORE="Târgoviște Punct",
    )
    def test_admin_sees_aggregates_but_no_credentials_or_contact_data(self):
        TOTPDevice.objects.create(user=self.admin, name="Telefon", confirmed=True)
        supplier = Supplier.objects.create(name="Furnizor test")
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 31))
        DocumentProcessingJob.objects.create(invoice=invoice)
        MetroScrapeJob.objects.create(start_url="https://example.test/catalog")
        ActivityLog.objects.create(
            user=self.operator,
            method="POST",
            path="/app/test/",
            status_code=403,
            outcome=ActivityLog.Outcome.DENIED,
        )
        self.client.force_login(self.admin)

        response = self.client.get("/app/operare/")

        self.assertContains(response, "Centru de operare")
        self.assertContains(response, "Târgoviște Punct")
        self.assertContains(response, "Accesări refuzate / 24h")
        self.assertNotContains(response, "never-render-this-secret")
        self.assertNotContains(response, "private-key-must-stay-hidden")
        self.assertNotContains(response, "secret-host")
        self.assertNotContains(response, "owner-secret@example.test")

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from comparator.models import Invoice, InvoiceLine, Supplier, SupplierParsingProfile
from comparator.services.invoices import process_invoice
from comparator.services.supplier_profiles import refresh_supplier_profile_metrics


class SupplierParsingProfileTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="profile-admin",
            password="A-test-password-2026!",
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.supplier = Supplier.objects.create(name="Furnizor cu profil")

    @override_settings(OLLAMA_ENABLED=False)
    def test_processing_uses_profile_and_records_parser_metrics(self):
        SupplierParsingProfile.objects.create(
            supplier=self.supplier,
            parser_mode=SupplierParsingProfile.ParserMode.HEURISTIC,
            apply_default_vat=True,
            default_vat_rate=Decimal("11"),
        )
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 26),
            ocr_text="Lapte 1L - 2 bucati x 6,50 RON",
        )

        process_invoice(invoice)

        profile = self.supplier.parsing_profile
        profile.refresh_from_db()
        self.assertEqual(profile.documents_processed, 1)
        self.assertEqual(profile.extracted_lines, 1)
        self.assertEqual(profile.last_parser, "heuristic")
        self.assertEqual(invoice.lines.get().vat_rate, Decimal("11"))

    def test_metrics_count_only_confirmed_corrections(self):
        invoice = Invoice.objects.create(supplier=self.supplier, issued_at=date(2026, 8, 26))
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="Corectat",
            match_score=100,
            match_corrected=True,
            needs_review=False,
            unit_price_gross=Decimal("1"),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="În lucru",
            needs_review=True,
            unit_price_gross=Decimal("1"),
        )

        profile = refresh_supplier_profile_metrics(self.supplier)

        self.assertEqual(profile.confirmed_lines, 1)
        self.assertEqual(profile.corrected_lines, 1)
        self.assertEqual(profile.correction_rate, 100)

    def test_staff_can_update_supplier_profile(self):
        response = self.client.post(
            f"/app/furnizori/{self.supplier.pk}/parsare/",
            {"parser_mode": "HEURISTIC", "apply_default_vat": "on", "default_vat_rate": "11"},
        )
        self.assertEqual(response.status_code, 302)
        profile = self.supplier.parsing_profile
        self.assertEqual(profile.parser_mode, SupplierParsingProfile.ParserMode.HEURISTIC)
        self.assertTrue(profile.apply_default_vat)

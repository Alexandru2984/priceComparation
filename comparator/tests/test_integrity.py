from datetime import date
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from comparator.models import BaseUnit, Invoice, InvoiceLine, MetroOffer, Product, Supplier
from comparator.services.integrity import audit_data_integrity


class DataIntegrityAuditTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Furnizor audit")
        self.product = Product.objects.create(name="Produs audit", base_unit=BaseUnit.PIECE)
        self.invoice = Invoice.objects.create(supplier=self.supplier, issued_at=date(2026, 8, 31))

    def test_clean_database_passes_command(self):
        output = StringIO()

        call_command("audit_data_integrity", stdout=output)

        self.assertIn("0 erori, 0 avertismente", output.getvalue())

    def test_confirmed_unmatched_line_fails_gate(self):
        InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="Linie fără asociere",
            quantity=1,
            unit_price_gross=2,
            needs_review=False,
        )

        report = audit_data_integrity()

        self.assertEqual(report["error_count"], 1)
        with self.assertRaisesMessage(CommandError, "Audit finalizat"):
            call_command("audit_data_integrity")

    def test_warning_can_be_promoted_to_failed_gate(self):
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=1,
            unit_size=1,
            price_gross=0,
            valid_from=date(2026, 8, 31),
        )

        report = audit_data_integrity()

        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["warning_count"], 1)
        with self.assertRaisesMessage(CommandError, "1 avertismente"):
            call_command("audit_data_integrity", "--fail-on-warnings")

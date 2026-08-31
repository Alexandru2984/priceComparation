from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from comparator.models import Invoice, InvoiceLine, Supplier


class VerifyPriceMatchCommandTests(TestCase):
    def test_clean_install_with_admin_passes(self):
        get_user_model().objects.create_superuser("owner", password="Strong-test-password-2026!")
        output = StringIO()

        call_command("verify_pricematch", stdout=output)

        self.assertIn("PriceMatch este pregătit", output.getvalue())

    def test_missing_admin_and_inconsistent_data_fail_gate(self):
        supplier = Supplier.objects.create(name="Furnizor gate")
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 31))
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="Linie confirmată fără produs",
            quantity=1,
            unit_price_gross=1,
            needs_review=False,
        )

        with self.assertRaisesMessage(CommandError, "niciun administrator activ"):
            call_command("verify_pricematch")

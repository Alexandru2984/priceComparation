from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile

from comparator.models import Invoice, InvoiceLine, MetroOffer, Product, Supplier


class ComparisonTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Distribuție SRL")
        self.product = Product.objects.create(name="Coca-Cola PET 2L", brand="Coca-Cola", base_unit="L")
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=6,
            unit_size=2,
            price_gross=Decimal("42.00"),
            valid_from=date(2026, 7, 14),
        )
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=1,
            unit_size=2,
            price_gross=Decimal("7.20"),
            valid_from=date(2026, 7, 14),
        )
        self.invoice = Invoice.objects.create(supplier=self.supplier, issued_at=date(2026, 7, 14))

    def test_comparison_normalizes_different_package_sizes(self):
        line = InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="Cca Cola 2L",
            quantity=3,
            units_per_package=1,
            unit_size=2,
            base_unit="L",
            unit_price_gross=Decimal("7.90"),
            matched_product=self.product,
            needs_review=False,
        )
        result = line.comparison()
        self.assertEqual(line.total_base_quantity, Decimal("6"))
        self.assertEqual(result["metro_price"], Decimal("3.50"))
        self.assertEqual(result["invoice_price"], Decimal("3.95"))
        self.assertEqual(result["total_impact"], Decimal("2.70"))
        self.assertEqual(result["status"], "MAI_SCUMP")


class DashboardSmokeTests(TestCase):
    def test_dashboard_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PriceMatch Local")

    def test_receipt_accepts_multiple_images(self):
        supplier = Supplier.objects.create(name="Magazin test")
        uploads = [
            SimpleUploadedFile("bon-1.jpg", b"prima", content_type="image/jpeg"),
            SimpleUploadedFile("bon-2.jpg", b"a doua", content_type="image/jpeg"),
        ]
        response = self.client.post(
            "/facturi/adauga/",
            {
                "document_type": Invoice.DocumentType.RECEIPT,
                "supplier": supplier.pk,
                "number": "R1",
                "issued_at": "2026-07-14",
                "documents": uploads,
                "ocr_text": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(number="R1")
        self.assertEqual(invoice.pages.count(), 2)

    def test_confirming_new_metro_line_creates_product_and_offer(self):
        supplier = Supplier.objects.create(name="METRO Automat", is_metro=True)
        invoice = Invoice.objects.create(
            supplier=supplier,
            document_type=Invoice.DocumentType.RECEIPT,
            issued_at=date(2026, 7, 14),
        )
        response = self.client.post(
            f"/facturi/{invoice.pk}/linie/adauga/",
            {
                "original_name": "Produs Metro Nou 1L",
                "quantity": "2",
                "units_per_package": "1",
                "unit_size": "1",
                "base_unit": "L",
                "unit_price_gross": "9.50",
                "vat_rate": "11",
                "line_total_gross": "19.00",
                "matched_product": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        product = Product.objects.get(name="Produs Metro Nou 1L")
        self.assertEqual(product.metro_offers.get().price_gross, Decimal("9.50"))

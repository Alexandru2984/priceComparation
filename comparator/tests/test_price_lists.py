from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from comparator.models import Invoice, Product, Supplier, SupplierPriceImport
from comparator.services.price_lists import create_price_list_invoice, parse_supplier_price_list


class SupplierPriceListTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser(
            username="price-list-admin",
            password="A-test-password-2026!",
        )
        self.client.force_login(self.staff)
        self.supplier = Supplier.objects.create(name="Distribuitor listă")
        self.product = Product.objects.create(name="Lapte integral 1L", base_unit="L")

    def _csv(self):
        return SimpleUploadedFile(
            "lista.csv",
            "Denumire;Pret cu TVA;EAN;Gramaj;Unitate;Bucati per bax\nLapte integral 1L;6,50;;1;L;6\nRand gresit;;;;;\n".encode(),
            content_type="text/csv",
        )

    def test_csv_preview_recognizes_romanian_columns_and_errors(self):
        rows = parse_supplier_price_list(self._csv(), self.supplier)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["price"], "6.50")
        self.assertEqual(rows[0]["units_per_package"], "6")
        self.assertEqual(rows[0]["base_unit"], "L")
        self.assertEqual(rows[0]["product_id"], self.product.pk)
        self.assertIn("Preț invalid", rows[1]["errors"])

    def test_confirm_is_idempotent_and_creates_review_document(self):
        rows = parse_supplier_price_list(self._csv(), self.supplier)
        price_import = SupplierPriceImport.objects.create(
            supplier=self.supplier,
            effective_at=date(2026, 8, 26),
            original_filename="lista.csv",
            rows=rows,
            row_count=2,
            warning_count=1,
            created_by=self.staff,
        )

        invoice, created = create_price_list_invoice(price_import)
        duplicate, duplicate_created = create_price_list_invoice(price_import)

        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate, invoice)
        self.assertEqual(invoice.document_type, Invoice.DocumentType.PRICE_LIST)
        self.assertEqual(invoice.status, Invoice.Status.REVIEW)
        self.assertEqual(invoice.lines.count(), 1)
        line = invoice.lines.get()
        self.assertEqual(line.unit_price_gross, Decimal("6.50"))
        self.assertTrue(line.needs_review)

    def test_upload_requires_preview_before_confirm(self):
        response = self.client.post(
            "/app/furnizori/liste-pret/importa/",
            {"supplier": self.supplier.pk, "effective_at": "2026-08-26", "file": self._csv()},
        )
        self.assertEqual(response.status_code, 302)
        price_import = SupplierPriceImport.objects.get()
        self.assertEqual(price_import.status, SupplierPriceImport.Status.DRAFT)
        self.assertIsNone(price_import.imported_invoice)

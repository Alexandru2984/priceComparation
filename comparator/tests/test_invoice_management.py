from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from comparator.models import DocumentPage, Invoice, InvoiceLine, MetroOffer, Product, Supplier
from comparator.services.invoices import sync_metro_offer_from_line


class InvoiceManagementTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_superuser(
            username="document-admin", password="A-test-password-2026!"
        )
        self.client.force_login(self.staff)
        self.supplier = Supplier.objects.create(name="Furnizor editare")

    def test_financial_fields_can_be_edited_after_lines_exist_but_identity_is_locked(self):
        other_supplier = Supplier.objects.create(name="Furnizor care nu trebuie aplicat")
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            number="INV-LOCKED",
            issued_at=date(2026, 8, 9),
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="Produs test",
            quantity=1,
            unit_price_gross=Decimal("10"),
        )
        response = self.client.post(
            f"/app/facturi/{invoice.pk}/editeaza/",
            {
                "document_type": Invoice.DocumentType.RECEIPT,
                "supplier": other_supplier.pk,
                "number": "SCHIMBAT",
                "issued_at": "2026-08-08",
                "transport_gross": "3.50",
                "document_discount_gross": "1.25",
                "document_total_gross": "12.25",
                "notes": "Corecție financiară",
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.supplier, self.supplier)
        self.assertEqual(invoice.number, "INV-LOCKED")
        self.assertEqual(invoice.issued_at, date(2026, 8, 9))
        self.assertEqual(invoice.transport_gross, Decimal("3.50"))
        self.assertEqual(invoice.document_total_gross, Decimal("12.25"))

    def test_identity_can_be_corrected_before_lines_are_added(self):
        other_supplier = Supplier.objects.create(name="Furnizor corect")
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            number="GRESIT",
            issued_at=date(2026, 8, 9),
        )
        response = self.client.post(
            f"/app/facturi/{invoice.pk}/editeaza/",
            {
                "document_type": Invoice.DocumentType.RECEIPT,
                "supplier": other_supplier.pk,
                "number": "CORECT",
                "issued_at": "2026-08-08",
                "transport_gross": "0",
                "document_discount_gross": "0",
                "document_total_gross": "10",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.supplier, other_supplier)
        self.assertEqual(invoice.number, "CORECT")
        self.assertEqual(invoice.document_type, Invoice.DocumentType.RECEIPT)

    def test_delete_requires_explicit_confirmation(self):
        invoice = Invoice.objects.create(supplier=self.supplier, issued_at=date(2026, 8, 9))
        response = self.client.post(
            f"/app/facturi/{invoice.pk}/sterge/", {"confirmation": "da"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(Invoice.objects.filter(pk=invoice.pk).exists())

    def test_deleting_last_line_resets_document_status(self):
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 9),
            status=Invoice.Status.REVIEW,
        )
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name="Linie de șters",
            quantity=1,
            unit_price_gross=Decimal("4"),
        )
        response = self.client.post(f"/app/linii/{line.pk}/sterge/")
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.NEW)

    def test_delete_removes_files_lines_and_derived_metro_offer(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            metro = Supplier.objects.create(name="METRO ștergere", is_metro=True)
            product = Product.objects.create(name="Produs METRO șters", base_unit="BUC")
            invoice = Invoice.objects.create(
                supplier=metro,
                number="M-DELETE",
                issued_at=date(2026, 8, 9),
            )
            page = DocumentPage.objects.create(
                invoice=invoice,
                page_order=1,
                file=SimpleUploadedFile("bon.jpg", b"continut-test", content_type="image/jpeg"),
            )
            file_path = Path(page.file.path)
            line = InvoiceLine.objects.create(
                invoice=invoice,
                original_name=product.name,
                quantity=1,
                unit_price_gross=Decimal("7.50"),
                matched_product=product,
                needs_review=False,
            )
            sync_metro_offer_from_line(line)
            self.assertTrue(file_path.exists())
            self.assertTrue(MetroOffer.objects.filter(product=product).exists())

            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/app/facturi/{invoice.pk}/sterge/", {"confirmation": "STERGE"}
                )

            self.assertEqual(response.status_code, 302)
            self.assertFalse(Invoice.objects.filter(pk=invoice.pk).exists())
            self.assertFalse(MetroOffer.objects.filter(product=product).exists())
            self.assertFalse(file_path.exists())

    def test_reprocessing_warning_is_shown_when_document_has_lines(self):
        invoice = Invoice.objects.create(supplier=self.supplier, issued_at=date(2026, 8, 9))
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="Linie existentă",
            quantity=1,
            unit_price_gross=Decimal("1"),
        )
        response = self.client.post(f"/app/facturi/{invoice.pk}/proceseaza/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Noua extracție OCR va înlocui toate liniile existente")
        self.assertEqual(invoice.lines.count(), 1)

    def test_correcting_metro_match_removes_offer_from_old_product(self):
        metro = Supplier.objects.create(name="METRO corecție", is_metro=True)
        old_product = Product.objects.create(name="Produs asociat greșit", base_unit="BUC")
        new_product = Product.objects.create(name="Produs asociat corect", base_unit="BUC")
        invoice = Invoice.objects.create(
            supplier=metro,
            number="M-CORRECT",
            issued_at=date(2026, 8, 9),
        )
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name="PRODUS BON",
            quantity=1,
            units_per_package=1,
            unit_size=1,
            base_unit="BUC",
            unit_price_gross=Decimal("8"),
            matched_product=old_product,
            needs_review=False,
        )
        sync_metro_offer_from_line(line)

        response = self.client.post(
            f"/app/linii/{line.pk}/editeaza/",
            {
                "original_name": "PRODUS BON",
                "ean": "",
                "quantity": "1",
                "units_per_package": "1",
                "unit_size": "1",
                "base_unit": "BUC",
                "unit_price_gross": "8",
                "vat_rate": "11",
                "line_total_gross": "8",
                "discount_gross": "0",
                "deposit_gross": "0",
                "matched_product": str(new_product.pk),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(MetroOffer.objects.filter(product=old_product).exists())
        self.assertTrue(MetroOffer.objects.filter(product=new_product).exists())

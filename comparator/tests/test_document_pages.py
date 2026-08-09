import io
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from comparator.models import DocumentPage, Invoice, InvoiceLine, Supplier


def valid_jpeg(name="pagina.jpg", color="white"):
    content = io.BytesIO()
    Image.new("RGB", (80, 100), color).save(content, format="JPEG")
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


class DocumentPageManagementTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="pages-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)
        self.supplier = Supplier.objects.create(name="Furnizor pagini")
        self.invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 9),
            status=Invoice.Status.PROCESSED,
        )

    def test_adds_pages_at_end_and_marks_existing_lines_for_review(self):
        InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="Linie deja extrasă",
            quantity=1,
            unit_price_gross=Decimal("5"),
            needs_review=False,
        )
        response = self.client.post(
            f"/app/facturi/{self.invoice.pk}/pagini/adauga/",
            {"documents": [valid_jpeg("doi.jpg"), valid_jpeg("trei.jpg", "gray")]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(self.invoice.pages.values_list("page_order", flat=True)), [1, 2])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.REVIEW)
        self.assertIn("Reprocesează OCR", self.invoice.processing_error)

    def test_invalid_image_is_rejected_without_storage(self):
        response = self.client.post(
            f"/app/facturi/{self.invoice.pk}/pagini/adauga/",
            {"documents": SimpleUploadedFile("fals.jpg", b"not-an-image", content_type="image/jpeg")},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.invoice.pages.exists())

    def test_maximum_file_count_includes_existing_pages(self):
        for order in range(1, 13):
            DocumentPage.objects.create(
                invoice=self.invoice,
                page_order=order,
                file=valid_jpeg(f"existent-{order}.jpg"),
            )
        response = self.client.post(
            f"/app/facturi/{self.invoice.pk}/pagini/adauga/",
            {"documents": valid_jpeg("prea-mult.jpg")},
            follow=True,
        )
        self.assertContains(response, "maximum 12 fișiere")
        self.assertEqual(self.invoice.pages.count(), 12)

    def test_combined_size_limit_is_enforced_before_storage(self):
        with patch("comparator.services.documents.MAX_DOCUMENT_TOTAL_SIZE", 1):
            response = self.client.post(
                f"/app/facturi/{self.invoice.pk}/pagini/adauga/",
                {"documents": valid_jpeg("prea-mare-impreuna.jpg")},
                follow=True,
            )
        self.assertContains(response, "maximum 50 MB în total")
        self.assertFalse(self.invoice.pages.exists())

    def test_moves_pages_and_ocr_order_follows_page_order(self):
        first = DocumentPage.objects.create(invoice=self.invoice, page_order=1, file=valid_jpeg("unu.jpg"))
        second = DocumentPage.objects.create(invoice=self.invoice, page_order=2, file=valid_jpeg("doi.jpg"))
        third = DocumentPage.objects.create(invoice=self.invoice, page_order=3, file=valid_jpeg("trei.jpg"))

        response = self.client.post(
            f"/app/documente/pagini/{third.pk}/muta/", {"direction": "up"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(self.invoice.pages.order_by("page_order").values_list("pk", flat=True)),
            [first.pk, third.pk, second.pk],
        )

    def test_delete_removes_file_and_compacts_page_numbers(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            first = DocumentPage.objects.create(
                invoice=self.invoice, page_order=1, file=valid_jpeg("unu.jpg")
            )
            second = DocumentPage.objects.create(
                invoice=self.invoice, page_order=2, file=valid_jpeg("doi.jpg")
            )
            third = DocumentPage.objects.create(
                invoice=self.invoice, page_order=3, file=valid_jpeg("trei.jpg")
            )
            deleted_path = Path(second.file.path)
            self.assertTrue(deleted_path.exists())

            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(f"/app/documente/pagini/{second.pk}/sterge/")

            self.assertEqual(response.status_code, 302)
            self.assertFalse(deleted_path.exists())
            self.assertEqual(
                list(self.invoice.pages.order_by("page_order").values_list("pk", "page_order")),
                [(first.pk, 1), (third.pk, 2)],
            )

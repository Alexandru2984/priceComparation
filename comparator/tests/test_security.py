import io
from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from PIL import Image

from comparator.models import DocumentPage, Invoice, Product, Supplier


def valid_jpeg(name="bon.jpg"):
    content = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(content, format="JPEG")
    return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")


class AccessControlTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.staff = users.objects.create_user(
            username="secure-admin", password="A-test-password-2026!", is_staff=True
        )
        self.regular = users.objects.create_user(username="regular", password="A-test-password-2026!")

    def test_public_demo_does_not_read_or_render_private_products(self):
        Product.objects.create(name="PRODUS SECRET INTERN", base_unit="BUC")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "fără date reale")
        self.assertNotContains(response, "PRODUS SECRET INTERN")

    def test_anonymous_user_is_redirected_from_private_app(self):
        response = self.client.get("/app/")
        self.assertRedirects(response, "/admin/login/?next=/app/", fetch_redirect_response=False)

    def test_non_staff_user_cannot_open_private_app(self):
        self.client.force_login(self.regular)
        response = self.client.get("/app/")
        self.assertRedirects(response, "/admin/login/?next=/app/", fetch_redirect_response=False)

    def test_staff_user_can_open_private_app(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/app/").status_code, 200)

    def test_security_headers_are_present(self):
        response = self.client.get("/")
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("camera=()", response["Permissions-Policy"])

    def test_private_post_rejects_missing_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        response = client.post("/app/furnizori/adauga/", {"name": "Atac CSRF"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Supplier.objects.filter(name="Atac CSRF").exists())


class UploadSecurityTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="upload-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)
        self.supplier = Supplier.objects.create(name="Furnizor upload")

    def test_fake_image_is_rejected_before_storage(self):
        response = self.client.post(
            "/app/facturi/adauga/",
            {
                "document_type": Invoice.DocumentType.RECEIPT,
                "supplier": self.supplier.pk,
                "number": "BAD-1",
                "issued_at": "2026-07-14",
                "documents": SimpleUploadedFile("malware.jpg", b"not-an-image", content_type="image/jpeg"),
                "ocr_text": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nu este o imagine validă")
        self.assertFalse(Invoice.objects.filter(number="BAD-1").exists())

    def test_fake_pdf_header_is_not_enough_to_pass_validation(self):
        response = self.client.post(
            "/app/facturi/adauga/",
            {
                "document_type": Invoice.DocumentType.INVOICE,
                "supplier": self.supplier.pk,
                "number": "BAD-PDF",
                "issued_at": "2026-07-14",
                "documents": SimpleUploadedFile("fals.pdf", b"%PDF-not-a-document", content_type="application/pdf"),
                "ocr_text": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "nu este un PDF valid")
        self.assertFalse(Invoice.objects.filter(number="BAD-PDF").exists())

    def test_document_download_requires_staff(self):
        with TemporaryDirectory() as directory:
            with override_settings(MEDIA_ROOT=directory):
                invoice = Invoice.objects.create(supplier=self.supplier, issued_at=date(2026, 7, 14))
                page = DocumentPage.objects.create(invoice=invoice, file=valid_jpeg(), page_order=1)
                self.client.logout()
                private_url = f"/app/documente/pagini/{page.pk}/fisier/"
                response = self.client.get(private_url)
                self.assertEqual(response.status_code, 302)
                self.assertEqual(self.client.get(page.file.url).status_code, 404)
                self.client.force_login(self.staff)
                response = self.client.get(private_url)
                self.assertEqual(response.status_code, 200)
                self.assertIn("attachment", response["Content-Disposition"])
                self.assertEqual(response["Cache-Control"], "private, no-store")

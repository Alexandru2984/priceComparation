import io
from datetime import date
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, TestCase, override_settings
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from PIL import Image

from pricecompare.security import get_client_ip_address

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
        self.assertRedirects(response, "/account/login/?next=/app/", fetch_redirect_response=False)

    def test_non_staff_user_cannot_open_private_app(self):
        self.client.force_login(self.regular)
        response = self.client.get("/app/")
        self.assertRedirects(response, "/account/login/?next=/app/", fetch_redirect_response=False)

    def test_staff_user_can_open_private_app(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/app/").status_code, 200)

    @override_settings(MFA_REQUIRED=True)
    def test_online_mode_forces_staff_without_device_to_enroll_mfa(self):
        self.client.force_login(self.staff)
        response = self.client.get("/app/")
        self.assertRedirects(
            response,
            "/account/two_factor/setup/?next=/app/",
            fetch_redirect_response=False,
        )

    @override_settings(MFA_REQUIRED=True)
    def test_verified_operator_can_open_app_but_not_django_admin(self):
        device = TOTPDevice.objects.create(user=self.staff, name="Telefon", confirmed=True)
        self.client.force_login(self.staff)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        self.assertEqual(self.client.get("/app/").status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 403)

    @override_settings(MFA_REQUIRED=True)
    def test_verified_administrator_can_open_django_admin(self):
        owner = get_user_model().objects.create_superuser(
            username="owner", password="A-test-password-2026!"
        )
        device = TOTPDevice.objects.create(user=owner, name="Telefon", confirmed=True)
        self.client.force_login(owner)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_security_headers_are_present(self):
        response = self.client.get("/")
        self.assertIn("default-src 'self'", response["Content-Security-Policy"])
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("camera=()", response["Permissions-Policy"])

    def test_login_and_mfa_pages_are_never_cached(self):
        response = self.client.get("/account/login/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_private_post_rejects_missing_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        response = client.post("/app/furnizori/adauga/", {"name": "Atac CSRF"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Supplier.objects.filter(name="Atac CSRF").exists())

    def test_axes_protects_primary_login_not_only_django_admin(self):
        from django.conf import settings

        self.assertFalse(settings.AXES_ONLY_ADMIN_SITE)

    @override_settings(
        TRUST_REVERSE_PROXY=True,
        TRUSTED_REVERSE_PROXY_IPS={"127.0.0.1"},
    )
    def test_axes_uses_real_ip_rewritten_by_trusted_reverse_proxy(self):
        request = RequestFactory().get(
            "/account/login/",
            HTTP_X_REAL_IP="203.0.113.42",
            REMOTE_ADDR="127.0.0.1",
        )
        self.assertEqual(get_client_ip_address(request), "203.0.113.42")

    @override_settings(
        TRUST_REVERSE_PROXY=True,
        TRUSTED_REVERSE_PROXY_IPS={"127.0.0.1"},
    )
    def test_real_ip_header_is_ignored_from_untrusted_peer(self):
        request = RequestFactory().get(
            "/account/login/",
            HTTP_X_REAL_IP="203.0.113.42",
            REMOTE_ADDR="198.51.100.8",
        )
        self.assertEqual(get_client_ip_address(request), "198.51.100.8")


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

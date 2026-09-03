import io
from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from comparator.models import Invoice, InvoiceLine, MetroOffer, MetroOfferTier, Product, Supplier
from comparator.services.insights import recent_metro_changes


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

    def test_comparison_applies_volume_price_only_after_package_threshold(self):
        bulk_offer = self.product.metro_offers.get(units_per_package=6)
        MetroOfferTier.objects.create(
            offer=bulk_offer,
            min_packages=2,
            price_gross=Decimal("36.00"),
        )
        line = InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="Coca Cola 2L baxuri",
            quantity=12,
            units_per_package=1,
            unit_size=2,
            base_unit="L",
            unit_price_gross=Decimal("7.00"),
            matched_product=self.product,
            needs_review=False,
        )

        result = line.comparison()

        self.assertEqual(result["metro_packages"], 2)
        self.assertTrue(result["metro_volume_applied"])
        self.assertEqual(result["metro_price"], Decimal("3.00"))

    def test_volume_price_is_not_used_below_threshold(self):
        offer = self.product.metro_offers.get(units_per_package=6)
        MetroOfferTier.objects.create(offer=offer, min_packages=2, price_gross=Decimal("36.00"))
        self.assertEqual(offer.price_for_packages(1), Decimal("42.00"))
        self.assertEqual(offer.price_for_packages(2), Decimal("36.00"))

    def test_volume_saving_summary_uses_the_best_tier(self):
        offer = self.product.metro_offers.get(units_per_package=6)
        MetroOfferTier.objects.create(offer=offer, min_packages=3, price_gross=Decimal("39.00"))
        best_tier = MetroOfferTier.objects.create(
            offer=offer,
            min_packages=6,
            price_gross=Decimal("36.00"),
        )

        self.assertEqual(offer.best_volume_tier, best_tier)
        self.assertEqual(offer.volume_saving_per_package, Decimal("6.00"))
        self.assertEqual(offer.volume_saving_percent, Decimal("14.28571428571428571428571429"))
        self.assertEqual(offer.minimum_volume_saving, Decimal("36.00"))
        self.assertEqual(offer.minimum_volume_spend, Decimal("216.00"))

    @override_settings(PREFERRED_METRO_STORE="METRO PUNCT TARGOVISTE")
    def test_current_offer_prefers_configured_store(self):
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=1,
            unit_size=2,
            price_gross=Decimal("8.00"),
            valid_from=date(2026, 7, 15),
            source="Selenium METRO PALLADY",
        )
        preferred = MetroOffer.objects.create(
            product=self.product,
            units_per_package=1,
            unit_size=2,
            price_gross=Decimal("8.40"),
            valid_from=date(2026, 7, 15),
            source="Selenium METRO PUNCT TARGOVISTE",
        )
        self.assertEqual(self.product.current_metro_offer(), preferred)

    def test_prefetched_current_offer_does_not_run_extra_queries(self):
        product = Product.objects.prefetch_related("metro_offers").get(pk=self.product.pk)
        with self.assertNumQueries(0):
            offer = product.current_metro_offer()
        self.assertIsNotNone(offer)


class DashboardSmokeTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="admin-test", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)

    @staticmethod
    def image_upload(name):
        content = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(content, format="JPEG")
        return SimpleUploadedFile(name, content.getvalue(), content_type="image/jpeg")

    @staticmethod
    def heic_upload(name="bon-iphone.heic"):
        content = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(content, format="HEIF")
        return SimpleUploadedFile(name, content.getvalue(), content_type="image/heic")

    def test_dashboard_loads(self):
        response = self.client.get("/app/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PriceMatch")
        self.assertContains(response, "Pregătire pentru primele date reale")

    @override_settings(METRO_PRICE_ANOMALY_PERCENT=Decimal("40"))
    def test_dashboard_shows_only_comparable_non_anomalous_package_changes(self):
        comparable = Product.objects.create(name="Produs cu schimbare validă", base_unit="BUC")
        package_shift = Product.objects.create(name="Produs cu ambalaj schimbat", base_unit="BUC")
        anomaly = Product.objects.create(name="Produs cu abatere", base_unit="BUC")
        source = "Selenium METRO TEST"
        MetroOffer.objects.create(
            product=comparable,
            units_per_package=1,
            unit_size=1,
            price_gross=10,
            valid_from=date(2026, 8, 30),
            source=source,
        )
        MetroOffer.objects.create(
            product=comparable,
            units_per_package=1,
            unit_size=1,
            price_gross=12,
            valid_from=date(2026, 9, 1),
            source=source,
        )
        MetroOffer.objects.create(
            product=package_shift,
            units_per_package=80,
            unit_size=1,
            price_gross=44,
            valid_from=date(2026, 8, 30),
            source=source,
        )
        MetroOffer.objects.create(
            product=package_shift,
            units_per_package=4,
            unit_size=1,
            price_gross=44,
            valid_from=date(2026, 9, 1),
            source=source,
        )
        MetroOffer.objects.create(
            product=anomaly,
            units_per_package=1,
            unit_size=1,
            price_gross=10,
            valid_from=date(2026, 8, 30),
            source=source,
        )
        MetroOffer.objects.create(
            product=anomaly,
            units_per_package=1,
            unit_size=1,
            price_gross=100,
            valid_from=date(2026, 9, 1),
            source=source,
        )
        cache.clear()

        changes = recent_metro_changes()
        response = self.client.get("/app/")

        self.assertEqual([change["offer"].product for change in changes], [comparable])
        self.assertContains(response, "Produs cu schimbare validă")
        self.assertContains(response, "lei/pachet")
        self.assertNotContains(response, "Produs cu ambalaj schimbat")
        self.assertNotContains(response, "Produs cu abatere")

    @patch("comparator.views_admin.system_readiness")
    def test_readiness_page_renders_private_diagnostics(self, readiness):
        readiness.return_value = {
            "ok_count": 1,
            "warning_count": 1,
            "error_count": 0,
            "checks": [
                {"name": "OCR Tesseract", "status": "OK", "detail": "Disponibil.", "action": ""},
                {"name": "Ollama", "status": "WARN", "detail": "Oprit.", "action": "Pornește serviciul"},
            ],
        }
        response = self.client.get("/app/stare/")
        self.assertContains(response, "OCR Tesseract")
        self.assertContains(response, "Pornește serviciul")

    def test_public_demo_loads(self):
        self.client.logout()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Date demonstrative")

    def test_document_form_exposes_mobile_camera_and_gallery_controls(self):
        response = self.client.get("/app/facturi/adauga/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Încarcă direct de pe telefon")
        self.assertContains(response, 'name="camera_documents"')
        self.assertContains(response, 'capture="environment"')
        self.assertContains(response, 'data-gallery-input="true"')
        self.assertContains(response, "Alege poze sau PDF")
        self.assertContains(response, ".heic,.heif")

    def test_receipt_accepts_camera_and_gallery_images_together(self):
        supplier = Supplier.objects.create(name="Magazin mobil")
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            response = self.client.post(
                "/app/facturi/adauga/",
                {
                    "document_type": Invoice.DocumentType.RECEIPT,
                    "supplier": supplier.pk,
                    "number": "MOBILE-1",
                    "issued_at": "2026-09-03",
                    "camera_documents": [self.image_upload("camera.jpg")],
                    "documents": [self.image_upload("galerie.jpg")],
                    "ocr_text": "",
                    "notes": "",
                },
            )

            self.assertEqual(response.status_code, 302)
            invoice = Invoice.objects.get(number="MOBILE-1")
            self.assertEqual(invoice.pages.count(), 2)
            self.assertTrue(invoice.pages.get(page_order=1).file.name.endswith("camera.jpg"))
            self.assertTrue(invoice.pages.get(page_order=2).file.name.endswith("galerie.jpg"))

    def test_receipt_accepts_heic_photo_from_iphone(self):
        supplier = Supplier.objects.create(name="Magazin iPhone")
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            response = self.client.post(
                "/app/facturi/adauga/",
                {
                    "document_type": Invoice.DocumentType.RECEIPT,
                    "supplier": supplier.pk,
                    "number": "IPHONE-HEIC",
                    "issued_at": "2026-09-03",
                    "documents": [self.heic_upload()],
                    "ocr_text": "",
                    "notes": "",
                },
            )

            self.assertEqual(response.status_code, 302)
            page = Invoice.objects.get(number="IPHONE-HEIC").pages.get()
            self.assertTrue(page.file.name.endswith("bon-iphone.heic"))

    def test_receipt_accepts_multiple_images(self):
        supplier = Supplier.objects.create(name="Magazin test")
        uploads = [
            self.image_upload("bon-1.jpg"),
            self.image_upload("bon-2.jpg"),
        ]
        response = self.client.post(
            "/app/facturi/adauga/",
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

    @override_settings(DATA_UPLOAD_MAX_NUMBER_FILES=20)
    def test_combined_mobile_upload_respects_twelve_file_limit(self):
        supplier = Supplier.objects.create(name="Magazin limită mobilă")
        response = self.client.post(
            "/app/facturi/adauga/",
            {
                "document_type": Invoice.DocumentType.RECEIPT,
                "supplier": supplier.pk,
                "number": "MOBILE-LIMIT",
                "issued_at": "2026-09-03",
                "camera_documents": [self.image_upload(f"camera-{index}.jpg") for index in range(7)],
                "documents": [self.image_upload(f"galerie-{index}.jpg") for index in range(6)],
                "ocr_text": "",
                "notes": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "maximum 12 imagini/PDF-uri")
        self.assertFalse(Invoice.objects.filter(number="MOBILE-LIMIT").exists())

    def test_confirming_new_metro_line_creates_product_and_offer(self):
        supplier = Supplier.objects.create(name="METRO Automat", is_metro=True)
        invoice = Invoice.objects.create(
            supplier=supplier,
            document_type=Invoice.DocumentType.RECEIPT,
            issued_at=date(2026, 7, 14),
        )
        response = self.client.post(
            f"/app/facturi/{invoice.pk}/linie/adauga/",
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

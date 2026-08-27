from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from comparator.models import Invoice, InvoiceLine, MetroOffer, PriceAlert, Product, Supplier


class ScaledCatalogViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            username="scale-admin", password="A-test-password-2026!", is_staff=True
        )
        cls.products = [
            Product(name=f"Produs scalare {index:03d}", brand="Test", base_unit="BUC")
            for index in range(125)
        ]
        Product.objects.bulk_create(cls.products)

    def setUp(self):
        self.client.force_login(self.staff)

    def test_catalog_is_paginated_at_one_hundred_rows(self):
        response = self.client.get("/app/catalog/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rows"]), 100)
        self.assertEqual(response.context["page_obj"].paginator.count, 125)
        second = self.client.get("/app/catalog/?page=2")
        self.assertEqual(len(second.context["rows"]), 25)

    def test_product_search_is_staff_only_and_limited(self):
        response = self.client.get("/app/catalog/cauta/?q=scalare")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["products"]), 20)
        self.assertIn("label", response.json()["products"][0])
        self.client.logout()
        anonymous = self.client.get("/app/catalog/cauta/?q=scalare")
        self.assertEqual(anonymous.status_code, 302)

    def test_alert_form_uses_autocomplete_without_rendering_catalog_options(self):
        response = self.client.get("/app/alerte/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-product-autocomplete")
        self.assertNotContains(response, "Produs scalare 124")

    def test_metro_offers_are_paginated(self):
        product = Product.objects.first()
        MetroOffer.objects.bulk_create([
            MetroOffer(product=product, price_gross=index + 1, valid_from=date(2026, 8, 9), source=f"Sursa {index}")
            for index in range(105)
        ])
        response = self.client.get("/app/metro/?location=all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["offers"]), 100)
        self.assertEqual(response.context["page_obj"].paginator.count, 105)


class InvoiceAutocompleteTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="invoice-scale-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)

    def test_bulk_review_keeps_selected_product_label(self):
        supplier = Supplier.objects.create(name="Furnizor autocomplete")
        product = Product.objects.create(name="Produs selectat", brand="Marca", base_unit="BUC")
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 9))
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="P SELECTAT",
            unit_price_gross=5,
            matched_product=product,
            needs_review=True,
        )
        response = self.client.get(f"/app/facturi/{invoice.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-product-autocomplete")
        self.assertContains(response, "Produs selectat · Marca")

    def test_existing_alert_renders_without_large_select(self):
        product = Product.objects.create(name="Produs alertă autocomplete", base_unit="KG")
        PriceAlert.objects.create(product=product, target_price=10)
        response = self.client.get("/app/alerte/")
        self.assertEqual(response.status_code, 200)
        self.assertLess(len(response.content), 20_000)

    def test_barcode_assignment_without_product_fails_safely(self):
        response = self.client.post(
            "/app/catalog/ean/asociaza/", {"code": "4006381333931", "product": ""}
        )
        self.assertRedirects(response, "/app/catalog/scaneaza-ean/")

    def test_receiving_scan_prefills_product_and_learns_supplier_code(self):
        supplier = Supplier.objects.create(name="Furnizor recepție")
        product = Product.objects.create(name="Produs recepționat", base_unit="BUC")
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 25))
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name="PROD REC",
            quantity=1,
            unit_price_gross=Decimal("5"),
            matched_product=product,
            needs_review=False,
        )
        response = self.client.get(f"/app/catalog/scaneaza-ean/?line={line.pk}")
        self.assertContains(response, "Produs recepționat")

        response = self.client.post(
            "/app/catalog/ean/asociaza/",
            {"code": "4006381333931", "product": product.pk, "line": line.pk},
        )

        self.assertRedirects(response, f"/app/facturi/{invoice.pk}/")
        line.refresh_from_db()
        product.refresh_from_db()
        self.assertEqual(product.ean, "4006381333931")
        self.assertEqual(line.ean, "4006381333931")

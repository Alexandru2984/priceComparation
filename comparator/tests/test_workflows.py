import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from comparator.models import (
    Invoice,
    InvoiceLine,
    MetroOffer,
    MetroScrapeJob,
    PriceAlert,
    Product,
    ProductAlias,
    ProductCode,
    ShoppingList,
    ShoppingListItem,
    Supplier,
    SupplierOffer,
)
from comparator.services.barcodes import assign_ean, is_valid_gtin
from comparator.services.insights import shopping_recommendation
from comparator.services.invoices import sync_supplier_offer_from_line
from comparator.services.matching import suggest_product
from comparator.services.metro_scraper import store_captured_rows
from comparator.services.ocr import merge_ocr_pages


class CostAndLearningTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Distribuitor test")
        self.product = Product.objects.create(name="Produs test", base_unit="BUC")
        self.invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 8),
            transport_gross=Decimal("18.50"),
            document_discount_gross=Decimal("9.25"),
        )

    def test_effective_cost_excludes_discount_and_sgr_and_allocates_document_costs(self):
        line = InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="Produs test",
            quantity=1,
            unit_price_gross=Decimal("100"),
            line_total_gross=Decimal("100"),
            discount_gross=Decimal("10"),
            deposit_gross=Decimal("5"),
            matched_product=self.product,
            needs_review=False,
        )
        InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="Alt produs",
            quantity=1,
            unit_price_gross=Decimal("100"),
            line_total_gross=Decimal("100"),
            needs_review=True,
        )
        self.assertEqual(line.merchandise_total_gross, Decimal("85"))
        self.assertEqual(line.landed_total_gross, Decimal("89.25"))
        self.assertEqual(line.price_per_base_unit, Decimal("89.25"))

    def test_confirmed_line_creates_supplier_history_and_exact_code_match(self):
        line = InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="ABREV PROD",
            ean="SKU-991",
            quantity=2,
            unit_price_gross=Decimal("8"),
            matched_product=self.product,
            needs_review=False,
        )
        sync_supplier_offer_from_line(line)
        self.assertTrue(SupplierOffer.objects.filter(invoice_line=line).exists())
        match, score = suggest_product("alt nume", self.supplier, "BUC", "SKU-991")
        self.assertEqual((match, score), (self.product, 100))


class BarcodeAndMetroIdentityTests(TestCase):
    def test_validates_and_assigns_gtin(self):
        product = Product.objects.create(name="EAN test", base_unit="BUC")
        self.assertTrue(is_valid_gtin("4006381333931"))
        self.assertFalse(is_valid_gtin("4006381333932"))
        assign_ean(product, "4006 3813 3393 1")
        product.refresh_from_db()
        self.assertEqual(product.ean, "4006381333931")
        self.assertTrue(ProductCode.objects.filter(product=product, kind="EAN").exists())

    def test_metro_code_prevents_duplicate_when_name_changes(self):
        product = Product.objects.create(name="Denumire veche", base_unit="L")
        ProductCode.objects.create(product=product, kind="METRO", code="BTY-SAME")
        job = MetroScrapeJob.objects.create(start_url="https://produse.metro.ro/shop")
        store_captured_rows(
            job,
            [{
                "external_id": "BTY-SAME",
                "name": "Denumire comercială nouă 1 L",
                "product_url": "https://produse.metro.ro/shop/pv/BTY-SAME/x",
                "store_name": "METRO PUNCT TARGOVISTE",
                "package_text": "1 BUCATA",
                "units_per_package": Decimal("1"),
                "unit_size": Decimal("1"),
                "base_unit": "L",
                "price_gross": Decimal("9.99"),
            }],
        )
        self.assertEqual(job.products.get().matched_product, product)
        self.assertEqual(job.products.get().match_score, 100)


class ShoppingAndAlertTests(TestCase):
    def test_recommends_cheapest_recent_source(self):
        supplier = Supplier.objects.create(name="Furnizor ieftin")
        product = Product.objects.create(name="Produs plan", base_unit="BUC")
        MetroOffer.objects.create(product=product, price_gross=10, valid_from=date(2026, 8, 8))
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 8))
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name=product.name,
            quantity=1,
            unit_price_gross=8,
            matched_product=product,
            needs_review=False,
        )
        sync_supplier_offer_from_line(line)
        shopping_list = ShoppingList.objects.create(name="Test")
        item = ShoppingListItem.objects.create(shopping_list=shopping_list, product=product, quantity=5)
        result = shopping_recommendation(item)
        self.assertEqual(result["best"]["source"], supplier.name)
        self.assertEqual(result["total"], Decimal("40.0000"))
        self.assertEqual(result["saving"], Decimal("10.0000"))

    def test_target_alert_triggers_at_equal_price(self):
        product = Product.objects.create(name="Produs alertă", base_unit="KG")
        MetroOffer.objects.create(product=product, price_gross=5, valid_from=date(2026, 8, 8))
        alert = PriceAlert.objects.create(product=product, target_price=5)
        self.assertTrue(alert.is_triggered)


class BulkReviewAndScannerViewsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="workflow-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)

    def test_bulk_review_confirms_and_learns_alias(self):
        supplier = Supplier.objects.create(name="Furnizor bulk")
        product = Product.objects.create(name="Produs asociat", base_unit="BUC")
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 8))
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name="P ASOC",
            quantity=2,
            unit_price_gross=4,
            needs_review=True,
        )
        response = self.client.post(
            f"/app/facturi/{invoice.pk}/revizuieste/",
            {
                "lines-TOTAL_FORMS": "1",
                "lines-INITIAL_FORMS": "1",
                "lines-MIN_NUM_FORMS": "0",
                "lines-MAX_NUM_FORMS": "1000",
                "lines-0-id": str(line.pk),
                "lines-0-original_name": "P ASOC",
                "lines-0-ean": "",
                "lines-0-quantity": "2",
                "lines-0-units_per_package": "1",
                "lines-0-unit_size": "1",
                "lines-0-base_unit": "BUC",
                "lines-0-unit_price_gross": "4",
                "lines-0-vat_rate": "11",
                "lines-0-line_total_gross": "8",
                "lines-0-discount_gross": "0",
                "lines-0-deposit_gross": "0",
                "lines-0-matched_product": str(product.pk),
            },
        )
        self.assertEqual(response.status_code, 302)
        line.refresh_from_db()
        self.assertFalse(line.needs_review)
        self.assertTrue(ProductAlias.objects.filter(supplier=supplier, alias="P ASOC", product=product).exists())
        self.assertTrue(SupplierOffer.objects.filter(invoice_line=line).exists())

    def test_scanner_page_allows_camera_only_for_itself(self):
        response = self.client.get("/app/catalog/scaneaza-ean/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("camera=(self)", response["Permissions-Policy"])
        self.assertIn("camera=()", self.client.get("/app/")["Permissions-Policy"])


class OCRAndBackupTests(TestCase):
    def test_removes_overlap_between_receipt_photos(self):
        merged = merge_ocr_pages([
            "MAGAZIN\nCOLA 1 x 8.00\nLAPTE 1 x 5.00",
            "COLA 1 x 8.00\nLAPTE 1 x 5.00\nPAINE 1 x 3.00",
        ])
        self.assertEqual(merged.count("COLA 1 x 8.00"), 1)
        self.assertEqual(merged.count("LAPTE 1 x 5.00"), 1)
        self.assertIn("PAINE 1 x 3.00", merged)

    def test_backup_has_verified_manifest_and_restore_requires_confirmation(self):
        with TemporaryDirectory() as directory:
            call_command("backup_pricematch", output=Path(directory), without_media=True, verbosity=0)
            backups = list(Path(directory).iterdir())
            self.assertEqual(len(backups), 1)
            manifest = json.loads((backups[0] / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("data.json.gz", manifest["files"])
            with self.assertRaises(CommandError):
                call_command("restore_pricematch", backups[0])

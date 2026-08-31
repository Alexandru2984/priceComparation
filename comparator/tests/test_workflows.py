import json
import gzip
import tarfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from comparator.models import (
    Invoice,
    InvoiceLine,
    InventoryItem,
    MetroOffer,
    MetroOfferTier,
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
from comparator.services.insights import optimize_shopping_list, profitability_analysis, shopping_recommendation
from comparator.services.invoices import sync_supplier_offer_from_line
from comparator.services.matching import suggest_product
from comparator.services.metro_scraper import store_captured_rows
from comparator.services.ocr import merge_ocr_pages
from comparator.management.commands.restore_pricematch import file_sha256, safe_members


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

    def test_document_total_reconciles_merchandise_adjustments_and_deposits(self):
        InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="Produs cu SGR",
            quantity=2,
            unit_price_gross=Decimal("10"),
            line_total_gross=Decimal("20"),
            discount_gross=Decimal("2"),
            deposit_gross=Decimal("1"),
        )
        self.invoice.document_total_gross = Decimal("27.25")
        self.invoice.save(update_fields=["document_total_gross"])
        self.assertEqual(self.invoice.calculated_document_total_gross, Decimal("27.25"))
        self.assertEqual(self.invoice.reconciliation_difference, Decimal("0.00"))
        self.assertTrue(self.invoice.is_reconciled)

    def test_line_exposes_suspicious_ocr_values_for_review(self):
        line = InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name="Produs OCR suspect",
            quantity=2,
            unit_price_gross=Decimal("10"),
            line_total_gross=Decimal("200"),
            vat_rate=Decimal("17"),
        )
        self.assertEqual(len(line.data_warnings), 2)
        self.assertIn("cantitate × preț", line.data_warnings[0])


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

    def test_shopping_list_uses_metro_volume_price_and_whole_packages(self):
        product = Product.objects.create(name="Apă bax 6 x 2 L", base_unit="L")
        offer = MetroOffer.objects.create(
            product=product,
            units_per_package=6,
            unit_size=2,
            price_gross=Decimal("12.00"),
            valid_from=date(2026, 8, 8),
        )
        MetroOfferTier.objects.create(offer=offer, min_packages=3, price_gross=Decimal("10.00"))
        shopping_list = ShoppingList.objects.create(name="Volum")
        item = ShoppingListItem.objects.create(shopping_list=shopping_list, product=product, quantity=25)

        result = shopping_recommendation(item)

        self.assertEqual(result["best"]["package_count"], 3)
        self.assertTrue(result["best"]["volume_applied"])
        self.assertEqual(result["total"], Decimal("30.00"))

    def test_supplier_offer_rounds_requirement_to_whole_packages(self):
        supplier = Supplier.objects.create(name="Furnizor baxuri")
        product = Product.objects.create(name="Produs la bax", base_unit="BUC")
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 25))
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name=product.name,
            quantity=1,
            units_per_package=6,
            unit_size=1,
            unit_price_gross=30,
            matched_product=product,
            needs_review=False,
        )
        sync_supplier_offer_from_line(line)
        shopping_list = ShoppingList.objects.create(name="Baxuri")
        item = ShoppingListItem.objects.create(shopping_list=shopping_list, product=product, quantity=7)

        result = shopping_recommendation(item)

        self.assertEqual(result["best"]["package_count"], 2)
        self.assertEqual(result["total"], Decimal("60.0000"))

    def test_optimizer_accounts_for_transport_once_per_supplier(self):
        supplier = Supplier.objects.create(name="Furnizor cu transport", transport_gross=20)
        product = Product.objects.create(name="Produs transport", base_unit="BUC")
        MetroOffer.objects.create(product=product, price_gross=10, valid_from=date(2026, 8, 25))
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 25))
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name=product.name,
            quantity=1,
            unit_price_gross=8,
            matched_product=product,
            needs_review=False,
        )
        sync_supplier_offer_from_line(line)
        shopping_list = ShoppingList.objects.create(name="Transport")
        ShoppingListItem.objects.create(shopping_list=shopping_list, product=product, quantity=1)

        result = optimize_shopping_list(shopping_list)

        self.assertEqual(result["rows"][0][1]["best"]["kind"], "METRO")
        self.assertEqual(result["total"], Decimal("10.00"))

    def test_optimizer_rejects_supplier_below_minimum_order(self):
        supplier = Supplier.objects.create(name="Furnizor cu minim", minimum_order_gross=100)
        product = Product.objects.create(name="Produs minim", base_unit="BUC")
        MetroOffer.objects.create(product=product, price_gross=10, valid_from=date(2026, 8, 25))
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 25))
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name=product.name,
            quantity=1,
            unit_price_gross=8,
            matched_product=product,
            needs_review=False,
        )
        sync_supplier_offer_from_line(line)
        shopping_list = ShoppingList.objects.create(name="Minim")
        ShoppingListItem.objects.create(shopping_list=shopping_list, product=product, quantity=1)

        result = optimize_shopping_list(shopping_list)

        self.assertEqual(result["rows"][0][1]["best"]["kind"], "METRO")
        self.assertFalse(result["has_minimum_warnings"])

    def test_budget_defers_low_priority_before_urgent_items(self):
        urgent = Product.objects.create(name="Urgent", base_unit="BUC")
        optional = Product.objects.create(name="Opțional", base_unit="BUC")
        MetroOffer.objects.create(product=urgent, price_gross=10, valid_from=date(2026, 8, 25))
        MetroOffer.objects.create(product=optional, price_gross=8, valid_from=date(2026, 8, 25))
        shopping_list = ShoppingList.objects.create(name="Buget", budget_gross=10)
        ShoppingListItem.objects.create(
            shopping_list=shopping_list,
            product=urgent,
            quantity=1,
            priority=ShoppingListItem.Priority.HIGH,
        )
        ShoppingListItem.objects.create(
            shopping_list=shopping_list,
            product=optional,
            quantity=1,
            priority=ShoppingListItem.Priority.LOW,
        )

        result = optimize_shopping_list(shopping_list)
        deferred_product_ids = [item.product_id for item, row in result["rows"] if row["deferred"]]

        self.assertEqual(deferred_product_ids, [optional.pk])
        self.assertEqual(result["total"], Decimal("10.00"))


class RetailMarginTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="margin-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)

    def test_margin_uses_net_vat_values_sale_unit_and_expected_waste(self):
        product = Product.objects.create(name="Produs marjă", base_unit="L")
        MetroOffer.objects.create(product=product, price_gross=11, valid_from=date(2026, 8, 25))
        item = InventoryItem.objects.create(
            product=product,
            retail_price_gross=Decimal("35.70"),
            retail_unit_size=2,
            purchase_vat_rate=10,
            retail_vat_rate=19,
            expected_waste_percent=20,
            target_margin_percent=25,
        )

        result = profitability_analysis(item)

        self.assertEqual(result["cost_per_sale_gross"], Decimal("22"))
        self.assertEqual(result["effective_cost_net"], Decimal("25"))
        self.assertEqual(result["retail_net"], Decimal("30"))
        self.assertAlmostEqual(result["margin_percent"], Decimal("16.6667"), places=4)
        self.assertAlmostEqual(result["recommended_retail_gross"], Decimal("39.6667"), places=4)
        self.assertEqual(result["status"], "BELOW_TARGET")

    def test_margin_is_incomplete_without_retail_price(self):
        product = Product.objects.create(name="Produs fără raft", base_unit="BUC")
        MetroOffer.objects.create(product=product, price_gross=5, valid_from=date(2026, 8, 25))
        result = profitability_analysis(InventoryItem.objects.create(product=product))
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertIsNone(result["margin_percent"])

    def test_staff_can_open_margin_analysis(self):
        product = Product.objects.create(name="Produs afișat în marje", base_unit="BUC")
        InventoryItem.objects.create(product=product, retail_price_gross=10)
        response = self.client.get("/app/marje/", secure=True)
        self.assertContains(response, product.name)


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

    def test_duplicate_number_for_same_supplier_and_date_is_rejected(self):
        supplier = Supplier.objects.create(name="Furnizor duplicate")
        Invoice.objects.create(supplier=supplier, number="INV-100", issued_at=date(2026, 8, 8))
        response = self.client.post(
            "/app/facturi/adauga/",
            {
                "document_type": Invoice.DocumentType.INVOICE,
                "supplier": supplier.pk,
                "number": "inv-100",
                "issued_at": "2026-08-08",
                "transport_gross": "0",
                "document_discount_gross": "0",
                "ocr_text": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Documentul există deja")
        self.assertEqual(Invoice.objects.filter(supplier=supplier).count(), 1)

    def test_database_constraint_still_allows_numberless_receipts(self):
        supplier = Supplier.objects.create(name="Furnizor bonuri fără număr")
        Invoice.objects.create(supplier=supplier, number="", issued_at=date(2026, 8, 8))
        Invoice.objects.create(supplier=supplier, number="", issued_at=date(2026, 8, 8))
        self.assertEqual(Invoice.objects.filter(supplier=supplier).count(), 2)

    def test_database_constraint_blocks_case_insensitive_duplicate(self):
        supplier = Supplier.objects.create(name="Furnizor constrângere")
        Invoice.objects.create(supplier=supplier, number="ABC-9", issued_at=date(2026, 8, 8))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Invoice.objects.create(supplier=supplier, number="abc-9", issued_at=date(2026, 8, 8))

    def test_document_list_filters_by_supplier_type_and_status(self):
        visible_supplier = Supplier.objects.create(name="Furnizor filtrat")
        hidden_supplier = Supplier.objects.create(name="Alt furnizor")
        Invoice.objects.create(
            supplier=visible_supplier,
            number="BON-CAUTAT",
            issued_at=date(2026, 8, 8),
            document_type=Invoice.DocumentType.RECEIPT,
            status=Invoice.Status.REVIEW,
        )
        Invoice.objects.create(
            supplier=hidden_supplier,
            number="FACT-ASCUNSA",
            issued_at=date(2026, 8, 8),
            document_type=Invoice.DocumentType.INVOICE,
            status=Invoice.Status.PROCESSED,
        )
        response = self.client.get("/app/facturi/?q=filtrat&type=RECEIPT&status=REVIEW")
        self.assertContains(response, "BON-CAUTAT")
        self.assertNotContains(response, "FACT-ASCUNSA")


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
            call_command("verify_backup_restore", backups[0], verbosity=0)
            with self.assertRaises(CommandError):
                call_command("restore_pricematch", backups[0])

    def test_restore_rejects_unexpected_manifest_files_before_touching_database(self):
        marker = Product.objects.create(name="Rămâne intact", base_unit="BUC")
        with TemporaryDirectory() as directory:
            backup = Path(directory)
            data_path = backup / "data.json.gz"
            with gzip.open(data_path, "wt", encoding="utf-8") as handle:
                handle.write("[]")
            manifest = {
                "format": 1,
                "files": {
                    "data.json.gz": file_sha256(data_path),
                    "../outside": "0" * 64,
                },
            }
            (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesMessage(CommandError, "fișiere nepermise"):
                call_command("restore_pricematch", backup, confirm="RESTORE", without_media=True)

        self.assertTrue(Product.objects.filter(pk=marker.pk).exists())

    def test_restore_rejects_traversal_links_and_special_archive_members(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory)
            for member in (
                tarfile.TarInfo("../escape"),
                tarfile.TarInfo("link"),
                tarfile.TarInfo("device"),
            ):
                archive = type("Archive", (), {"getmembers": lambda self, item=member: [item]})()
                if member.name == "link":
                    member.type = tarfile.SYMTYPE
                elif member.name == "device":
                    member.type = tarfile.CHRTYPE
                with self.assertRaises(CommandError):
                    list(safe_members(archive, destination))

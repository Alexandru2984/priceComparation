from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from comparator.models import InventoryItem, Product, SalesImport, SalesImportLine, StockMovement
from comparator.services.sales_imports import apply_sales_import, parse_sales_file


class SalesImportTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="pos-admin",
            password="A-test-password-2026!",
            is_staff=True,
        )
        self.client.force_login(self.staff)
        self.product = Product.objects.create(
            name="Apă minerală 2L",
            ean="5941234567890",
            base_unit="L",
        )
        self.inventory = InventoryItem.objects.create(
            product=self.product,
            minimum_quantity=2,
            target_quantity=10,
            retail_unit_size=Decimal("2"),
        )
        StockMovement.objects.create(
            inventory_item=self.inventory,
            quantity_delta=Decimal("20"),
            reason=StockMovement.Reason.OPENING,
        )

    def _file(self):
        return SimpleUploadedFile(
            "vanzari.csv",
            "EAN;Cantitate;Data vanzare;Numar bon\n5941234567890;2;26.08.2026 10:30;B100\n".encode(),
            content_type="text/csv",
        )

    def _create_import(self):
        file_hash, rows = parse_sales_file(self._file(), date(2026, 8, 26))
        sales_import = SalesImport.objects.create(
            original_filename="vanzari.csv",
            file_hash=file_hash,
            row_count=len(rows),
            created_by=self.staff,
        )
        SalesImportLine.objects.bulk_create([
            SalesImportLine(sales_import=sales_import, **row) for row in rows
        ])
        return sales_import

    def test_preview_matches_ean_without_changing_stock(self):
        sales_import = self._create_import()
        line = sales_import.lines.get()

        self.assertEqual(line.product, self.product)
        self.assertEqual(line.match_score, 100)
        self.assertEqual(self.inventory.current_quantity, Decimal("20"))

    def test_apply_uses_retail_unit_size_and_is_repeatable(self):
        sales_import = self._create_import()

        first = apply_sales_import(sales_import)
        second = apply_sales_import(sales_import)

        self.inventory.refresh_from_db()
        self.assertEqual(first, {"applied": 1, "duplicates": 0, "pending": 0})
        self.assertEqual(second, {"applied": 0, "duplicates": 0, "pending": 0})
        self.assertEqual(self.inventory.current_quantity, Decimal("16"))
        self.assertEqual(StockMovement.objects.filter(reason=StockMovement.Reason.SALE).count(), 1)

    def test_reimported_file_is_deduplicated_by_source_key(self):
        first_import = self._create_import()
        second_import = self._create_import()

        apply_sales_import(first_import)
        result = apply_sales_import(second_import)

        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(StockMovement.objects.filter(reason=StockMovement.Reason.SALE).count(), 1)
        self.assertEqual(self.inventory.current_quantity, Decimal("16"))

    def test_web_upload_only_creates_preview(self):
        response = self.client.post(
            "/app/stoc/vanzari/importa/",
            {"default_date": "2026-08-26", "file": self._file()},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SalesImport.objects.count(), 1)
        self.assertEqual(StockMovement.objects.filter(reason=StockMovement.Reason.SALE).count(), 0)

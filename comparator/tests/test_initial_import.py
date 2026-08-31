import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import load_workbook

from comparator.models import InitialDataImport, InventoryItem, Product, StockMovement, Supplier
from comparator.services.initial_import import (
    apply_initial_import,
    build_initial_workbook_template,
    parse_initial_workbook,
)


def populated_workbook():
    workbook = load_workbook(io.BytesIO(build_initial_workbook_template()))
    workbook["Furnizori"].append(["Distribuitor Test", "RO123", "Nu", 100, 20, 500, "Livrare marți"])
    workbook["Produse"].append(["Lapte integral 1L", "Marca Test", "5941234567899", "L", "Lactate", "Da"])
    workbook["Stoc"].append(["5941234567899", "Lapte integral 1L", 12, 3, 10, 8.5, 1, 11, 11, 25, 2])
    output = io.BytesIO()
    workbook.save(output)
    return SimpleUploadedFile(
        "import-initial.xlsx",
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


class InitialImportTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_superuser("initial-owner", password="A-test-password-2026!")
        self.operator = users.objects.create_user("initial-operator", password="A-test-password-2026!", is_staff=True)

    def test_template_has_required_sheets_and_parser_validates_rows(self):
        file_hash, rows = parse_initial_workbook(populated_workbook())

        self.assertEqual(len(file_hash), 64)
        self.assertEqual([row["kind"] for row in rows], ["SUPPLIER", "PRODUCT", "STOCK"])
        self.assertFalse(any(row["errors"] for row in rows))

    def test_apply_is_idempotent_and_creates_audited_opening_stock(self):
        file_hash, rows = parse_initial_workbook(populated_workbook())
        initial_import = InitialDataImport.objects.create(
            original_filename="import-initial.xlsx",
            file_hash=file_hash,
            rows=rows,
            row_count=len(rows),
            created_by=self.owner,
        )

        first = apply_initial_import(initial_import)
        second = apply_initial_import(initial_import)

        self.assertEqual(first["suppliers_created"], 1)
        self.assertEqual(first["products_created"], 1)
        self.assertEqual(first["opening_movements"], 1)
        self.assertTrue(second["already_applied"])
        self.assertEqual(Supplier.objects.count(), 1)
        product = Product.objects.get(ean="5941234567899")
        inventory = InventoryItem.objects.get(product=product)
        self.assertEqual(inventory.current_quantity, 12)
        self.assertEqual(StockMovement.objects.filter(inventory_item=inventory).count(), 1)

    def test_web_flow_previews_before_applying_and_reuses_same_file(self):
        self.client.force_login(self.owner)

        response = self.client.post("/app/configurare/import-initial/", {"file": populated_workbook()})

        initial_import = InitialDataImport.objects.get()
        self.assertRedirects(response, f"/app/configurare/import-initial/{initial_import.pk}/")
        self.assertFalse(Product.objects.exists())
        duplicate = self.client.post("/app/configurare/import-initial/", {"file": populated_workbook()})
        self.assertRedirects(duplicate, f"/app/configurare/import-initial/{initial_import.pk}/")
        self.assertEqual(InitialDataImport.objects.count(), 1)

        applied = self.client.post(f"/app/configurare/import-initial/{initial_import.pk}/aplica/")
        self.assertRedirects(applied, f"/app/configurare/import-initial/{initial_import.pk}/")
        self.assertTrue(Product.objects.filter(ean="5941234567899").exists())

    def test_semantically_identical_workbooks_ignore_generated_metadata(self):
        first_hash, _ = parse_initial_workbook(populated_workbook())
        second_hash, _ = parse_initial_workbook(populated_workbook())

        self.assertEqual(first_hash, second_hash)

    def test_operator_cannot_access_initial_import(self):
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get("/app/configurare/import-initial/").status_code, 403)
        self.assertEqual(self.client.get("/app/configurare/import-initial/sablon/").status_code, 403)

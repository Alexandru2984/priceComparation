from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from comparator.models import (
    InventoryItem,
    Invoice,
    InvoiceLine,
    Product,
    ShoppingList,
    StockMovement,
    Supplier,
)
from comparator.services.inventory import create_replenishment_list, sync_invoice_stock, sync_stock_from_line


class InventoryServiceTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Lapte stoc 1 L", base_unit="L")
        self.inventory = InventoryItem.objects.create(
            product=self.product,
            minimum_quantity=Decimal("5"),
            target_quantity=Decimal("12"),
        )

    def test_stock_is_audited_sum_and_replenishment_targets_policy(self):
        StockMovement.objects.create(
            inventory_item=self.inventory,
            quantity_delta=Decimal("4"),
            reason=StockMovement.Reason.OPENING,
        )
        StockMovement.objects.create(
            inventory_item=self.inventory,
            quantity_delta=Decimal("-1"),
            reason=StockMovement.Reason.SALE,
        )
        self.assertEqual(self.inventory.current_quantity, Decimal("3"))
        self.assertEqual(self.inventory.replenishment_quantity, Decimal("9"))

    def test_confirmed_opt_in_invoice_creates_idempotent_receipt(self):
        supplier = Supplier.objects.create(name="Furnizor stoc")
        invoice = Invoice.objects.create(
            supplier=supplier,
            issued_at=date(2026, 8, 25),
            receive_into_stock=True,
        )
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name="Lapte 1L",
            quantity=3,
            units_per_package=2,
            unit_size=1,
            base_unit="L",
            unit_price_gross=5,
            matched_product=self.product,
            needs_review=False,
        )
        sync_stock_from_line(line)
        sync_stock_from_line(line)
        self.assertEqual(StockMovement.objects.filter(invoice_line=line).count(), 1)
        self.assertEqual(self.inventory.current_quantity, Decimal("6"))

        invoice.receive_into_stock = False
        invoice.save(update_fields=["receive_into_stock"])
        sync_invoice_stock(invoice)
        self.assertFalse(StockMovement.objects.filter(invoice_line=line).exists())

    def test_replenishment_list_contains_only_low_products(self):
        StockMovement.objects.create(
            inventory_item=self.inventory,
            quantity_delta=Decimal("2"),
            reason=StockMovement.Reason.OPENING,
        )
        other = Product.objects.create(name="Produs suficient", base_unit="BUC")
        other_inventory = InventoryItem.objects.create(product=other, minimum_quantity=2, target_quantity=5)
        StockMovement.objects.create(
            inventory_item=other_inventory,
            quantity_delta=Decimal("3"),
            reason=StockMovement.Reason.OPENING,
        )
        shopping_list = create_replenishment_list("Test reaprovizionare")
        self.assertIsInstance(shopping_list, ShoppingList)
        item = shopping_list.items.get()
        self.assertEqual(item.product, self.product)
        self.assertEqual(item.quantity, Decimal("10"))


class InventoryViewTests(TestCase):
    def setUp(self):
        staff = get_user_model().objects.create_user(
            username="inventory-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(staff)

    def test_inventory_page_loads(self):
        self.assertEqual(self.client.get("/app/stoc/").status_code, 200)

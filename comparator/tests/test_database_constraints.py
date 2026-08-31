from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase

from comparator.models import (
    BaseUnit,
    InventoryItem,
    Invoice,
    InvoiceLine,
    MetroOffer,
    MetroOfferTier,
    PriceAlert,
    Product,
    ShoppingList,
    ShoppingListItem,
    Supplier,
    SupplierOffer,
)


class FinancialConstraintTests(TestCase):
    def setUp(self):
        self.supplier = Supplier.objects.create(name="Furnizor constrângeri")
        self.product = Product.objects.create(
            name="Produs constrângeri",
            base_unit=BaseUnit.PIECE,
        )
        self.invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 31),
        )
        self.line = InvoiceLine.objects.create(
            invoice=self.invoice,
            original_name=self.product.name,
            quantity=1,
            unit_price_gross=Decimal("5.00"),
            matched_product=self.product,
        )

    def test_invoice_line_rejects_invalid_quantities_money_and_scores(self):
        invalid_values = (
            {"quantity": 0},
            {"unit_price_gross": Decimal("-0.01")},
            {"vat_rate": Decimal("100.01")},
            {"match_score": 101},
        )
        for overrides in invalid_values:
            with self.subTest(overrides=overrides):
                values = {
                    "invoice": self.invoice,
                    "original_name": "Linie respinsă",
                    "quantity": 1,
                    "unit_price_gross": Decimal("1.00"),
                    **overrides,
                }
                with self.assertRaises(IntegrityError), transaction.atomic():
                    InvoiceLine.objects.create(**values)

    def test_metro_offer_and_volume_tier_reject_invalid_values(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MetroOffer.objects.create(
                product=self.product,
                units_per_package=0,
                unit_size=1,
                price_gross=1,
                valid_from=date(2026, 8, 31),
            )
        offer = MetroOffer.objects.create(
            product=self.product,
            units_per_package=1,
            unit_size=1,
            price_gross=1,
            valid_from=date(2026, 8, 31),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            MetroOfferTier.objects.create(offer=offer, min_packages=1, price_gross=1)

    def test_inventory_and_shopping_quantities_are_guarded_by_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            InventoryItem.objects.create(product=self.product, retail_unit_size=0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShoppingList.objects.create(name="Buget invalid", budget_gross=-1)
        shopping_list = ShoppingList.objects.create(name="Listă validă")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShoppingListItem.objects.create(
                shopping_list=shopping_list,
                product=self.product,
                quantity=0,
            )

    def test_reference_prices_cannot_be_negative(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SupplierOffer.objects.create(
                supplier=self.supplier,
                product=self.product,
                invoice_line=self.line,
                price_per_base_unit=Decimal("-0.01"),
                base_unit=BaseUnit.PIECE,
                valid_from=date(2026, 8, 31),
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PriceAlert.objects.create(product=self.product, target_price=Decimal("-0.01"))

    def test_nullable_financial_fields_remain_valid(self):
        inventory = InventoryItem.objects.create(product=self.product, retail_price_gross=None)
        shopping_list = ShoppingList.objects.create(name="Fără buget", budget_gross=None)
        alert = PriceAlert.objects.create(
            product=self.product,
            target_price=Decimal("1.00"),
            last_notified_price=None,
        )

        self.assertIsNone(inventory.retail_price_gross)
        self.assertIsNone(shopping_list.budget_gross)
        self.assertIsNone(alert.last_notified_price)

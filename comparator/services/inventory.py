from decimal import Decimal

from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from comparator.models import InventoryItem, ShoppingList, ShoppingListItem, StockMovement


def inventory_with_balance(queryset=None):
    queryset = queryset or InventoryItem.objects.all()
    return queryset.select_related("product").annotate(
        _current_quantity=Coalesce(
            Sum("movements__quantity_delta"),
            Value(Decimal("0")),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        )
    )


@transaction.atomic
def sync_stock_from_line(line):
    existing = StockMovement.objects.filter(invoice_line=line).first()
    should_receive = (
        line.invoice.receive_into_stock
        and not line.needs_review
        and bool(line.matched_product_id)
    )
    if not should_receive:
        if existing:
            existing.delete()
        return None
    inventory, _ = InventoryItem.objects.get_or_create(product=line.matched_product)
    movement, _ = StockMovement.objects.update_or_create(
        invoice_line=line,
        defaults={
            "inventory_item": inventory,
            "quantity_delta": line.total_base_quantity,
            "reason": StockMovement.Reason.RECEIPT,
            "note": f"{line.invoice.get_document_type_display()} {line.invoice.number or line.invoice_id}"[:240],
        },
    )
    return movement


@transaction.atomic
def sync_invoice_stock(invoice):
    synced = 0
    for line in invoice.lines.select_related("invoice", "matched_product"):
        if sync_stock_from_line(line):
            synced += 1
    if not invoice.receive_into_stock:
        StockMovement.objects.filter(invoice_line__invoice=invoice).delete()
    return synced


@transaction.atomic
def create_replenishment_list(name=None):
    items = list(inventory_with_balance(InventoryItem.objects.filter(active=True)))
    needed = [(item, item.replenishment_quantity) for item in items]
    needed = [(item, quantity) for item, quantity in needed if quantity > 0]
    if not needed:
        return None
    shopping_list = ShoppingList.objects.create(
        name=name or f"Reaprovizionare {timezone.localdate():%d.%m.%Y}"
    )
    ShoppingListItem.objects.bulk_create(
        [
            ShoppingListItem(
                shopping_list=shopping_list,
                product=item.product,
                quantity=quantity,
            )
            for item, quantity in needed
        ]
    )
    return shopping_list

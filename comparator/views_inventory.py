from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    InventoryItemForm,
    SalesImportLineForm,
    SalesImportUploadForm,
    ShoppingListForm,
    ShoppingListItemForm,
    StockMovementForm,
)
from .models import InventoryItem, SalesImport, SalesImportLine, ShoppingList, ShoppingListItem, StockMovement
from .services.insights import optimize_shopping_list, profitability_summary, source_option_prefetches
from .services.inventory import create_replenishment_list, inventory_with_balance
from .services.sales_imports import apply_sales_import, parse_sales_file


def _page_query(request):
    query = request.GET.copy()
    query.pop("page", None)
    return query.urlencode()


def shopping_list_index(request):
    form = ShoppingListForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        shopping_list = form.save()
        return redirect("comparator:shopping_list_detail", pk=shopping_list.pk)
    lists = ShoppingList.objects.annotate(item_count=Count("items"))
    return render(request, "comparator/shopping_list_index.html", {"lists": lists, "form": form})


def shopping_list_detail(request, pk):
    shopping_list = get_object_or_404(ShoppingList, pk=pk)
    form = ShoppingListItemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.shopping_list = shopping_list
        ShoppingListItem.objects.update_or_create(
            shopping_list=shopping_list,
            product=item.product,
            defaults={
                "quantity": item.quantity,
                "priority": item.priority,
                "purchased": item.purchased,
            },
        )
        messages.success(request, "Produsul a fost adăugat în listă.")
        return redirect("comparator:shopping_list_detail", pk=pk)
    analysis = optimize_shopping_list(shopping_list)
    return render(
        request,
        "comparator/shopping_list_detail.html",
        {
            "shopping_list": shopping_list,
            "rows": analysis["rows"],
            "form": form,
            "estimated_total": analysis["total"],
            "potential_saving": analysis["potential_saving"],
            "orders": analysis["orders"],
            "budget_remaining": analysis["budget_remaining"],
            "deferred_count": analysis["deferred_count"],
            "has_minimum_warnings": analysis["has_minimum_warnings"],
        },
    )


def shopping_list_edit(request, pk):
    shopping_list = get_object_or_404(ShoppingList, pk=pk)
    form = ShoppingListForm(request.POST or None, instance=shopping_list)
    if form.is_valid():
        form.save()
        messages.success(request, "Lista și bugetul au fost actualizate.")
        return redirect("comparator:shopping_list_detail", pk=pk)
    return render(request, "comparator/form.html", {"form": form, "title": "Editează lista și bugetul"})


def shopping_list_item_toggle(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(ShoppingListItem, pk=pk)
    item.purchased = not item.purchased
    item.save(update_fields=["purchased"])
    return redirect("comparator:shopping_list_detail", pk=item.shopping_list_id)


def shopping_list_item_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    item = get_object_or_404(ShoppingListItem, pk=pk)
    list_id = item.shopping_list_id
    item.delete()
    return redirect("comparator:shopping_list_detail", pk=list_id)


def inventory_index(request):
    if request.method == "POST" and not request.user.is_superuser:
        raise PermissionDenied("Configurarea stocului necesită rolul de administrator PriceMatch.")
    instance = None
    posted_product = request.POST.get("product", "") if request.method == "POST" else ""
    if posted_product.isdigit():
        instance = InventoryItem.objects.filter(product_id=posted_product).first()
    form = InventoryItemForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Politica de stoc a fost salvată.")
        return redirect("comparator:inventory_index")
    all_items = list(inventory_with_balance(InventoryItem.objects.filter(active=True)))
    low_count = sum(item.is_low for item in all_items)
    low_only = request.GET.get("low") == "1"
    items = [item for item in all_items if item.is_low] if low_only else all_items
    page_obj = Paginator(items, 100).get_page(request.GET.get("page"))
    return render(
        request,
        "comparator/inventory_list.html",
        {
            "form": form,
            "items": page_obj,
            "page_obj": page_obj,
            "page_query": _page_query(request),
            "low_only": low_only,
            "low_count": low_count,
            "recent_movements": StockMovement.objects.select_related(
                "inventory_item__product", "invoice_line__invoice"
            )[:12],
        },
    )


def sales_import_create(request):
    form = SalesImportUploadForm(request.POST or None, request.FILES or None, initial={"default_date": date.today()})
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            file_hash, rows = parse_sales_file(upload, form.cleaned_data["default_date"])
        except (ValueError, OSError) as exc:
            form.add_error("file", str(exc))
        else:
            sales_import = SalesImport.objects.create(
                original_filename=Path(upload.name).name[:255],
                file_hash=file_hash,
                row_count=len(rows),
                warning_count=sum(bool(row["error"]) or row["match_score"] < 75 for row in rows),
                created_by=request.user,
            )
            SalesImportLine.objects.bulk_create([SalesImportLine(sales_import=sales_import, **row) for row in rows])
            return redirect("comparator:sales_import_detail", pk=sales_import.pk)
    imports = SalesImport.objects.select_related("created_by")[:20]
    return render(request, "comparator/sales_import_create.html", {"form": form, "imports": imports})


def sales_import_detail(request, pk):
    sales_import = get_object_or_404(SalesImport.objects.select_related("created_by"), pk=pk)
    lines = sales_import.lines.select_related("product", "product__inventory")
    return render(
        request,
        "comparator/sales_import_detail.html",
        {"sales_import": sales_import, "lines": lines},
    )


def sales_import_line_edit(request, pk):
    line = get_object_or_404(SalesImportLine.objects.select_related("sales_import", "product"), pk=pk)
    if line.applied_at:
        messages.error(request, "O linie deja aplicată nu mai poate fi modificată.")
        return redirect("comparator:sales_import_detail", pk=line.sales_import_id)
    form = SalesImportLineForm(request.POST or None, instance=line)
    if form.is_valid():
        line = form.save(commit=False)
        if line.product_id and line.quantity > 0 and line.sold_at:
            line.error = ""
            line.match_score = 100
        line.save()
        messages.success(request, "Linia POS a fost actualizată.")
        return redirect("comparator:sales_import_detail", pk=line.sales_import_id)
    return render(request, "comparator/form.html", {"form": form, "title": "Corectează linia POS"})


def sales_import_apply(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    sales_import = get_object_or_404(SalesImport, pk=pk)
    result = apply_sales_import(sales_import)
    messages.success(
        request,
        f"Vânzări aplicate: {result['applied']}; duplicate ignorate: {result['duplicates']}; de rezolvat: {result['pending']}.",
    )
    return redirect("comparator:sales_import_detail", pk=pk)


def inventory_item_edit(request, pk):
    item = get_object_or_404(InventoryItem, pk=pk)
    form = InventoryItemForm(request.POST or None, instance=item)
    if form.is_valid():
        form.save()
        messages.success(request, "Politica de stoc a fost actualizată.")
        return redirect("comparator:inventory_index")
    return render(request, "comparator/form.html", {"form": form, "title": "Editează politica de stoc"})


def margin_analysis(request):
    query = request.GET.get("q", "").strip()
    items = (
        InventoryItem.objects.select_related("product")
        .prefetch_related(*source_option_prefetches("product__"))
        .filter(active=True)
    )
    if query:
        items = items.filter(Q(product__name__icontains=query) | Q(product__brand__icontains=query))
    summary = profitability_summary(items)
    status_order = {"LOSS": 0, "BELOW_TARGET": 1, "INCOMPLETE": 2, "ON_TARGET": 3}
    summary["rows"].sort(
        key=lambda row: (
            status_order[row[1]["status"]],
            row[1]["margin_percent"] if row[1]["margin_percent"] is not None else Decimal("0"),
            row[0].product.name,
        )
    )
    return render(request, "comparator/margin_analysis.html", {"summary": summary, "query": query})


def stock_movement_create(request, pk):
    item = get_object_or_404(InventoryItem.objects.select_related("product"), pk=pk)
    form = StockMovementForm(request.POST or None)
    if form.is_valid():
        movement = form.save(commit=False)
        movement.inventory_item = item
        movement.created_by = request.user
        movement.save()
        messages.success(request, "Mișcarea de stoc a fost înregistrată.")
        return redirect("comparator:inventory_index")
    return render(
        request,
        "comparator/form.html",
        {"form": form, "title": f"Mișcare stoc · {item.product.name}"},
    )


def inventory_replenishment_create(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    shopping_list = create_replenishment_list()
    if not shopping_list:
        messages.info(request, "Niciun produs nu este sub stocul minim.")
        return redirect("comparator:inventory_index")
    messages.success(request, f"Lista «{shopping_list.name}» a fost generată din stoc.")
    return redirect("comparator:shopping_list_detail", pk=shopping_list.pk)

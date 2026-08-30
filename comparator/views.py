import csv
import io
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib import messages
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, Min, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    InvoiceForm,
    InvoiceEditForm,
    DocumentPagesForm,
    InvoiceLineForm,
    InvoiceLineFormSet,
    InventoryItemForm,
    MetroImportForm,
    MetroOfferForm,
    PriceAlertForm,
    ProductForm,
    SalesImportLineForm,
    SalesImportUploadForm,
    ShoppingListForm,
    ShoppingListItemForm,
    StockMovementForm,
    SupplierForm,
    SupplierParsingProfileForm,
    SupplierPriceListUploadForm,
)
from .models import (
    AutomationRun,
    BaseUnit,
    DocumentPage,
    DocumentProcessingJob,
    Invoice,
    InvoiceLine,
    InvoiceRevision,
    InventoryItem,
    MetroOffer,
    MetroOfferTier,
    MetroPriceAnomaly,
    MetroProductState,
    MetroScrapeJob,
    PriceAlert,
    Product,
    ProductCode,
    ProductAlias,
    PushSubscription,
    SalesImport,
    SalesImportLine,
    ShoppingList,
    ShoppingListItem,
    StockMovement,
    Supplier,
    SupplierParsingProfile,
    SupplierPriceImport,
)
from .services.barcodes import assign_ean, is_valid_gtin, normalize_barcode
from .services.documents import add_document_pages, delete_document_page, move_document_page
from .services.invoices import (
    delete_invoice,
    delete_invoice_line,
    reconcile_derived_metro_offer,
    restore_invoice_revision,
    sync_all_confirmed_metro_lines,
    sync_metro_offer_from_line,
    sync_supplier_offer_from_line,
)
from .services.inventory import create_replenishment_list, inventory_with_balance, sync_invoice_stock, sync_stock_from_line
from .services.health import system_readiness
from .services.exports import build_catalog_csv, build_catalog_xlsx
from .services.matching import apply_match
from .services.metro_scraper import (
    import_scraped_rows,
    launch_alphabet_catalog_job,
    launch_breadth_catalog_job,
    launch_mass_catalog_job,
    launch_scrape_job,
)
from .services.metro_sitemap import (
    MetroSitemapError,
    fetch_metro_sitemap_products,
    import_metro_sitemap_products,
)
from .services.insights import (
    catalog_quality_summary,
    matching_quality_summary,
    optimize_shopping_list,
    profitability_analysis,
    profitability_summary,
    product_history,
    recent_metro_changes,
)
from .services.notifications import is_allowed_push_endpoint, send_to_active_staff, webpush_configured
from .services.processing_queue import enqueue_document
from .services.price_lists import create_price_list_invoice, parse_supplier_price_list
from .services.sales_imports import apply_sales_import, parse_sales_file
from .services.weekly_reports import build_weekly_report, build_weekly_report_xlsx
from .services.supplier_profiles import refresh_supplier_profile_metrics


def dashboard(request):
    recent_invoices = Invoice.objects.select_related("supplier")[:6]
    review_count = InvoiceLine.objects.filter(needs_review=True).count()
    comparisons = []
    for line in InvoiceLine.objects.select_related("invoice", "invoice__supplier", "matched_product"):
        comparison = line.comparison()
        if comparison:
            comparisons.append((line, comparison))
    comparisons.sort(key=lambda item: abs(item[1]["total_impact"]), reverse=True)
    total_impact = sum((item[1]["total_impact"] for item in comparisons), Decimal("0"))
    alerts = [alert for alert in PriceAlert.objects.select_related("product").filter(active=True) if alert.is_triggered]
    supplier_count = Supplier.objects.count()
    document_count = Invoice.objects.count()
    inventory_count = InventoryItem.objects.filter(active=True).count()
    return render(
        request,
        "comparator/dashboard.html",
        {
            "recent_invoices": recent_invoices,
            "review_count": review_count,
            "product_count": Product.objects.filter(active=True).count(),
            "offer_count": MetroOffer.objects.filter(active=True).count(),
            "comparisons": comparisons[:8],
            "total_impact": total_impact,
            "triggered_alerts": alerts[:6],
            "metro_changes": recent_metro_changes(),
            "quality": catalog_quality_summary(),
            "matching_quality": matching_quality_summary(),
            "open_metro_anomalies": MetroPriceAnomaly.objects.filter(
                status=MetroPriceAnomaly.Status.OPEN
            ).count(),
            "recent_automation": AutomationRun.objects.select_related("metro_job")[:5],
            "supplier_count": supplier_count,
            "document_count": document_count,
            "inventory_count": inventory_count,
        },
    )


def readiness(request):
    report = system_readiness()
    return render(request, "comparator/readiness.html", {"report": report})


def weekly_report(request):
    end_value = request.GET.get("end", "")
    try:
        end_date = date.fromisoformat(end_value) if end_value else timezone.localdate()
    except ValueError:
        end_date = timezone.localdate()
        messages.warning(request, "Data raportului nu a fost validă; s-a folosit data curentă.")
    report = build_weekly_report(end_date)
    return render(request, "comparator/weekly_report.html", {"report": report})


def weekly_report_export(request):
    end_value = request.GET.get("end", "")
    try:
        end_date = date.fromisoformat(end_value) if end_value else timezone.localdate()
    except ValueError:
        end_date = timezone.localdate()
    report = build_weekly_report(end_date)
    response = HttpResponse(
        build_weekly_report_xlsx(report),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="raport-saptamanal-{end_date.isoformat()}.xlsx"'
    return response


def matching_quality(request):
    lines = (
        InvoiceLine.objects.select_related("invoice", "invoice__supplier", "matched_product")
        .filter(needs_review=True)
        .order_by("-invoice__issued_at", "id")
    )
    page_obj = Paginator(lines, 100).get_page(request.GET.get("page"))
    return render(
        request,
        "comparator/matching_quality.html",
        {
            "summary": matching_quality_summary(),
            "lines": page_obj,
            "page_obj": page_obj,
            "page_query": _page_query(request),
        },
    )


def supplier_list(request):
    suppliers = Supplier.objects.annotate(invoice_count=Count("invoices"))
    return render(request, "comparator/supplier_list.html", {"suppliers": suppliers})


def supplier_create(request):
    form = SupplierForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Furnizorul a fost adăugat.")
        return redirect("comparator:supplier_list")
    return render(request, "comparator/form.html", {"form": form, "title": "Furnizor nou"})


def supplier_parsing_profile(request, pk):
    supplier = get_object_or_404(Supplier, pk=pk)
    profile = refresh_supplier_profile_metrics(supplier)
    form = SupplierParsingProfileForm(request.POST or None, instance=profile)
    if form.is_valid():
        form.save()
        messages.success(request, "Regulile locale de parsare au fost actualizate.")
        return redirect("comparator:supplier_parsing_profile", pk=supplier.pk)
    recent_documents = supplier.invoices.prefetch_related("lines").order_by("-issued_at", "-id")[:20]
    return render(
        request,
        "comparator/supplier_parsing_profile.html",
        {"supplier": supplier, "profile": profile, "form": form, "recent_documents": recent_documents},
    )


def supplier_price_import_create(request):
    form = SupplierPriceListUploadForm(request.POST or None, request.FILES or None, initial={"effective_at": date.today()})
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            rows = parse_supplier_price_list(upload, form.cleaned_data["supplier"])
        except (ValueError, OSError) as exc:
            form.add_error("file", str(exc))
        else:
            price_import = SupplierPriceImport.objects.create(
                supplier=form.cleaned_data["supplier"],
                effective_at=form.cleaned_data["effective_at"],
                original_filename=Path(upload.name).name[:255],
                rows=rows,
                row_count=len(rows),
                warning_count=sum(bool(row["errors"]) or row["match_score"] < 75 for row in rows),
                created_by=request.user,
            )
            return redirect("comparator:supplier_price_import_detail", pk=price_import.pk)
    recent_imports = SupplierPriceImport.objects.select_related("supplier", "imported_invoice")[:20]
    return render(
        request,
        "comparator/supplier_price_import_create.html",
        {"form": form, "recent_imports": recent_imports},
    )


def supplier_price_import_detail(request, pk):
    price_import = get_object_or_404(
        SupplierPriceImport.objects.select_related("supplier", "imported_invoice", "created_by"),
        pk=pk,
    )
    return render(request, "comparator/supplier_price_import_detail.html", {"price_import": price_import})


def supplier_price_import_confirm(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    price_import = get_object_or_404(SupplierPriceImport, pk=pk)
    try:
        invoice, created = create_price_list_invoice(price_import)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("comparator:supplier_price_import_detail", pk=pk)
    messages.success(
        request,
        "Lista a fost transformată într-un document de revizuit."
        if created else "Lista fusese deja importată; am deschis documentul existent.",
    )
    return redirect("comparator:invoice_detail", pk=invoice.pk)


def _filtered_products(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    products = Product.objects.prefetch_related("metro_offers__volume_tiers")
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(brand__icontains=query) | Q(ean__icontains=query)
        )
    if category:
        products = products.filter(category=category)
    return products, query, category


def product_list(request):
    products, query, category = _filtered_products(request)
    page_obj = Paginator(products, 100).get_page(request.GET.get("page"))
    rows = []
    for product in page_obj:
        offer = product.current_metro_offer()
        rows.append((product, offer))
    return render(
        request,
        "comparator/product_list.html",
        {
            "rows": rows,
            "page_obj": page_obj,
            "page_query": _page_query(request),
            "query": query,
            "selected_category": category,
            "categories": Product.objects.exclude(category="").values_list("category", flat=True).distinct().order_by("category"),
            "preferred_metro_store": settings.PREFERRED_METRO_STORE,
        },
    )


def _page_query(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def product_search(request):
    query = request.GET.get("q", "").strip()[:100]
    if len(query) < 2:
        return JsonResponse({"products": []})
    products = Product.objects.filter(active=True).filter(
        Q(name__icontains=query)
        | Q(brand__icontains=query)
        | Q(ean__icontains=query)
        | Q(codes__code__icontains=query)
    ).distinct().order_by("name", "brand")[:20]
    result = []
    for product in products:
        label = product.name
        if product.brand:
            label += f" · {product.brand}"
        if product.ean:
            label += f" · {product.ean}"
        result.append({"id": product.pk, "label": label, "unit": product.base_unit})
    return JsonResponse({"products": result})


def catalog_export(request, file_format):
    products, _, _ = _filtered_products(request)
    products = list(products)
    if file_format == "csv":
        response = HttpResponse(build_catalog_csv(products), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="catalog-pricematch.csv"'
        return response
    if file_format == "xlsx":
        product_ids = [product.pk for product in products]
        offers = (
            MetroOffer.objects.filter(product_id__in=product_ids)
            .select_related("product")
            .prefetch_related("volume_tiers")
            .order_by("product__name", "-valid_from", "source")
        )
        response = HttpResponse(
            build_catalog_xlsx(products, offers),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="catalog-pricematch.xlsx"'
        return response
    raise Http404


def product_create(request):
    form = ProductForm(request.POST or None)
    if form.is_valid():
        product = form.save()
        if product.ean:
            assign_ean(product, product.ean)
        messages.success(request, "Produsul a fost adăugat în catalogul urmărit.")
        return redirect("comparator:product_list")
    return render(request, "comparator/form.html", {"form": form, "title": "Produs urmărit nou"})


def product_detail(request, pk):
    product = get_object_or_404(
        Product.objects.prefetch_related("metro_offers__volume_tiers", "supplier_offers"),
        pk=pk,
    )
    history, minimum, maximum = product_history(product)
    inventory = inventory_with_balance(InventoryItem.objects.filter(product=product)).first()
    profitability = profitability_analysis(inventory) if inventory else None
    return render(
        request,
        "comparator/product_detail.html",
        {
            "product": product,
            "history": history,
            "minimum": minimum,
            "maximum": maximum,
            "inventory": inventory,
            "profitability": profitability,
        },
    )


def price_alert_list(request):
    form = PriceAlertForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Alerta de preț a fost salvată.")
        return redirect("comparator:price_alert_list")
    alerts = PriceAlert.objects.select_related("product")
    page_obj = Paginator(alerts, 50).get_page(request.GET.get("page"))
    return render(
        request,
        "comparator/price_alert_list.html",
        {"alerts": page_obj, "page_obj": page_obj, "page_query": _page_query(request), "form": form},
    )


def price_alert_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    get_object_or_404(PriceAlert, pk=pk).delete()
    messages.success(request, "Alerta a fost ștearsă.")
    return redirect("comparator:price_alert_list")


def notification_settings(request):
    return render(
        request,
        "comparator/notification_settings.html",
        {
            "webpush_ready": webpush_configured(),
            "vapid_public_key": settings.WEBPUSH_VAPID_PUBLIC_KEY,
            "active_subscription_count": PushSubscription.objects.filter(
                user=request.user, active=True
            ).count(),
        },
    )


def push_subscribe(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not webpush_configured():
        return JsonResponse({"error": "Notificările nu sunt configurate pe server."}, status=503)
    if len(request.body) > 16_384:
        return JsonResponse({"error": "Datele abonamentului sunt prea mari."}, status=400)
    try:
        subscription = json.loads(request.body)
        endpoint = subscription["endpoint"].strip()
        p256dh = subscription["keys"]["p256dh"].strip()
        auth = subscription["keys"]["auth"].strip()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return JsonResponse({"error": "Abonament invalid."}, status=400)
    token_pattern = re.compile(r"^[A-Za-z0-9_-]{16,255}$")
    if (
        len(endpoint) > 1000
        or not is_allowed_push_endpoint(endpoint)
        or not token_pattern.fullmatch(p256dh)
        or not token_pattern.fullmatch(auth)
    ):
        return JsonResponse({"error": "Abonament invalid."}, status=400)
    existing = PushSubscription.objects.filter(endpoint=endpoint).first()
    if existing and existing.user_id != request.user.id:
        return JsonResponse({"error": "Abonamentul aparține altui cont."}, status=409)
    PushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": request.user,
            "p256dh": p256dh,
            "auth": auth,
            "user_agent": request.headers.get("User-Agent", "")[:300],
            "active": True,
        },
    )
    return JsonResponse({"ok": True})


def push_unsubscribe(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        endpoint = json.loads(request.body).get("endpoint", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({"error": "Cerere invalidă."}, status=400)
    PushSubscription.objects.filter(user=request.user, endpoint=endpoint).update(active=False)
    return JsonResponse({"ok": True})


def push_test(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    delivered = send_to_active_staff(
        {
            "title": "PriceMatch · test reușit",
            "body": "Telefonul poate primi alerte de preț.",
            "url": "/app/alerte/",
            "tag": "pricematch-test",
        },
        user=request.user,
    )
    if not delivered:
        return JsonResponse({"error": "Nu s-a putut livra notificarea."}, status=502)
    return JsonResponse({"ok": True, "delivered": delivered})


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
            SalesImportLine.objects.bulk_create([
                SalesImportLine(sales_import=sales_import, **row) for row in rows
            ])
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
    items = InventoryItem.objects.select_related("product").filter(active=True)
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


def metro_list(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    availability = request.GET.get("availability", "active").strip()
    volume = request.GET.get("volume", "all").strip()
    sort = request.GET.get("sort", "newest").strip()
    snapshot = request.GET.get("snapshot", "current").strip()
    location = request.GET.get("location", "preferred").strip()
    tracked_store = settings.PREFERRED_METRO_STORE.strip()
    offers = MetroOffer.objects.select_related("product").prefetch_related("volume_tiers")
    if availability == "inactive":
        offers = offers.filter(active=False)
    elif availability == "all":
        pass
    else:
        availability = "active"
        offers = offers.filter(active=True)
    if query:
        offers = offers.filter(
            Q(product__name__icontains=query) | Q(product__brand__icontains=query) | Q(source__icontains=query)
        )
    if category:
        offers = offers.filter(product__category=category)
    if location == "preferred" and tracked_store:
        offers = offers.filter(source__icontains=tracked_store)
    else:
        location = "all"
    if volume == "with":
        offers = offers.filter(volume_tiers__isnull=False).distinct()
    elif volume == "without":
        offers = offers.filter(volume_tiers__isnull=True)
    else:
        volume = "all"
    if snapshot == "history":
        pass
    else:
        snapshot = "current"
        latest_offer = MetroOffer.objects.filter(
            product_id=OuterRef("product_id"),
            source=OuterRef("source"),
        )
        if availability == "active":
            latest_offer = latest_offer.filter(active=True)
        elif availability == "inactive":
            latest_offer = latest_offer.filter(active=False)
        offers = offers.filter(
            pk=Subquery(latest_offer.order_by("-valid_from", "-pk").values("pk")[:1])
        )
    if sort == "saving":
        offers = offers.annotate(
            volume_saving=F("price_gross") - Min("volume_tiers__price_gross"),
        ).order_by(F("volume_saving").desc(nulls_last=True), "product__name")
    elif sort == "name":
        offers = offers.order_by("product__name", "-valid_from")
    else:
        sort = "newest"
        offers = offers.order_by("-valid_from", "product__name")
    page_obj = Paginator(offers, 100).get_page(request.GET.get("page"))
    confirmed_document_lines = InvoiceLine.objects.filter(
        invoice__supplier__is_metro=True, needs_review=False, matched_product__isnull=False
    ).count()
    states = MetroProductState.objects.all()
    if tracked_store:
        states = states.filter(store_name__iexact=tracked_store)
    freshness = states.aggregate(total=Count("id"), last_seen=Max("last_seen_at"))
    freshness.update(
        available=states.filter(available=True).count(),
        unavailable=states.filter(available=False).count(),
        stale=states.filter(
            available=True,
            last_seen_at__lt=timezone.now() - timedelta(days=14),
        ).count(),
    )
    current_volume_offers = MetroOffer.objects.filter(active=True)
    if tracked_store:
        current_volume_offers = current_volume_offers.filter(source__icontains=tracked_store)
    latest_active_offer = MetroOffer.objects.filter(
        product_id=OuterRef("product_id"),
        source=OuterRef("source"),
        active=True,
    ).order_by("-valid_from", "-pk")
    current_volume_offers = current_volume_offers.filter(
        pk=Subquery(latest_active_offer.values("pk")[:1]),
        volume_tiers__isnull=False,
    )
    return render(
        request,
        "comparator/metro_list.html",
        {
            "offers": page_obj,
            "page_obj": page_obj,
            "page_query": _page_query(request),
            "query": query,
            "selected_category": category,
            "availability": availability,
            "volume": volume,
            "sort": sort,
            "snapshot": snapshot,
            "location": location,
            "categories": Product.objects.exclude(category="").values_list("category", flat=True).distinct().order_by("category"),
            "confirmed_document_lines": confirmed_document_lines,
            "preferred_metro_store": settings.PREFERRED_METRO_STORE,
            "freshness": freshness,
            "active_volume_offer_count": current_volume_offers.distinct().count(),
        },
    )


def metro_offer_create(request):
    form = MetroOfferForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Prețul METRO a fost salvat.")
        return redirect("comparator:metro_list")
    return render(request, "comparator/form.html", {"form": form, "title": "Preț METRO nou"})


def metro_price_anomalies(request):
    status = request.GET.get("status", MetroPriceAnomaly.Status.OPEN)
    if status not in MetroPriceAnomaly.Status.values and status != "ALL":
        status = MetroPriceAnomaly.Status.OPEN
    anomalies = MetroPriceAnomaly.objects.select_related("product", "state", "job", "reviewed_by")
    if status != "ALL":
        anomalies = anomalies.filter(status=status)
    page_obj = Paginator(anomalies, 100).get_page(request.GET.get("page"))
    return render(
        request,
        "comparator/metro_price_anomalies.html",
        {
            "anomalies": page_obj,
            "page_obj": page_obj,
            "page_query": _page_query(request),
            "selected_status": status,
            "status_choices": MetroPriceAnomaly.Status.choices,
        },
    )


def metro_price_anomaly_review(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    anomaly = get_object_or_404(MetroPriceAnomaly, pk=pk)
    status = request.POST.get("status", "")
    if status not in {MetroPriceAnomaly.Status.CONFIRMED, MetroPriceAnomaly.Status.DISMISSED}:
        messages.error(request, "Selectează o decizie validă pentru abatere.")
        return redirect("comparator:metro_price_anomalies")
    anomaly.status = status
    anomaly.note = request.POST.get("note", "").strip()[:300]
    anomaly.reviewed_by = request.user
    anomaly.reviewed_at = timezone.now()
    anomaly.save(update_fields=["status", "note", "reviewed_by", "reviewed_at"])
    messages.success(request, "Abaterea de preț a fost revizuită.")
    return redirect("comparator:metro_price_anomalies")


def _decimal(value, default="1"):
    try:
        return Decimal((value or default).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, AttributeError):
        return Decimal(default)


@transaction.atomic
def _import_metro_file(upload):
    content = upload.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    required = {"name", "base_unit", "price_gross"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("CSV-ul trebuie să conțină coloanele: name, base_unit, price_gross.")

    imported = 0
    for row_number, row in enumerate(reader, start=2):
        if row_number > 10_001:
            raise ValueError("CSV-ul poate conține maximum 10.000 de produse.")
        name = (row.get("name") or "").strip()
        unit = (row.get("base_unit") or "").strip().upper()
        if not name or unit not in BaseUnit.values:
            raise ValueError(f"Linia {row_number}: nume lipsă sau base_unit invalid (BUC, KG, L).")
        ean = (row.get("ean") or "").strip()
        brand = (row.get("brand") or "").strip()
        lookup = {"ean": ean} if ean else {"name": name, "brand": brand, "base_unit": unit}
        product, _ = Product.objects.get_or_create(
            **lookup,
            defaults={"name": name, "brand": brand, "base_unit": unit, "ean": ean},
        )
        valid_from_raw = (row.get("valid_from") or "").strip()
        valid_from = date.fromisoformat(valid_from_raw) if valid_from_raw else date.today()
        offer = MetroOffer.objects.create(
            product=product,
            units_per_package=_decimal(row.get("units_per_package")),
            unit_size=_decimal(row.get("unit_size")),
            price_gross=_decimal(row.get("price_gross"), "0"),
            valid_from=valid_from,
            source=(row.get("source") or "METRO").strip(),
        )
        tier_min = (row.get("volume_min_packages") or "").strip()
        tier_price = (row.get("volume_price_gross") or "").strip()
        if bool(tier_min) != bool(tier_price):
            raise ValueError(
                f"Linia {row_number}: completează împreună volume_min_packages și volume_price_gross."
            )
        if tier_min:
            min_packages = int(tier_min)
            if min_packages < 2:
                raise ValueError(f"Linia {row_number}: pragul de volum trebuie să fie minimum 2.")
            MetroOfferTier.objects.create(
                offer=offer,
                min_packages=min_packages,
                price_gross=_decimal(tier_price, "0"),
            )
        imported += 1
    return imported


def metro_import(request):
    form = MetroImportForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        try:
            count = _import_metro_file(form.cleaned_data["file"])
        except (ValueError, UnicodeDecodeError) as exc:
            form.add_error("file", str(exc))
        else:
            messages.success(request, f"Au fost importate {count} prețuri METRO.")
            return redirect("comparator:metro_list")
    return render(request, "comparator/metro_import.html", {"form": form})


def metro_sync_documents(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    count = sync_all_confirmed_metro_lines()
    messages.success(request, f"Au fost sincronizate {count} linii confirmate din documentele METRO.")
    return redirect("comparator:metro_list")


def metro_scrape_list(request):
    jobs = MetroScrapeJob.objects.all()[:30]
    return render(
        request,
        "comparator/metro_scrape_list.html",
        {"jobs": jobs, "preferred_metro_store": settings.PREFERRED_METRO_STORE},
    )


def metro_scrape_start(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not settings.METRO_SCRAPER_ENABLED:
        raise PermissionDenied("Scanarea Selenium este dezactivată în acest mediu.")
    active = MetroScrapeJob.objects.filter(
        status__in=[MetroScrapeJob.Status.PENDING, MetroScrapeJob.Status.RUNNING]
    ).first()
    if active:
        messages.warning(request, "Există deja o scanare activă. Finalizeaz-o sau închide fereastra Chrome.")
        return redirect("comparator:metro_scrape_detail", pk=active.pk)
    job = MetroScrapeJob.objects.create(start_url=settings.METRO_START_URL)
    try:
        launch_scrape_job(job)
    except Exception as exc:
        job.status = MetroScrapeJob.Status.ERROR
        job.error = str(exc)
        job.save(update_fields=["status", "error"])
        messages.error(request, f"Browserul nu a putut fi pornit: {exc}")
    else:
        messages.success(request, "Chrome se deschide. Navighează pe METRO și folosește panoul verde PriceMatch.")
    return redirect("comparator:metro_scrape_detail", pk=job.pk)


def metro_scrape_mass_start(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not settings.METRO_SCRAPER_ENABLED:
        raise PermissionDenied("Scanarea Selenium este dezactivată în acest mediu.")
    active = MetroScrapeJob.objects.filter(
        status__in=[MetroScrapeJob.Status.PENDING, MetroScrapeJob.Status.RUNNING]
    ).first()
    if active:
        messages.warning(request, "Există deja o scanare activă.")
        return redirect("comparator:metro_scrape_detail", pk=active.pk)
    job = MetroScrapeJob.objects.create(
        start_url=settings.METRO_START_URL,
        scan_type=MetroScrapeJob.ScanType.FULL,
    )
    try:
        launch_mass_catalog_job(job, settings.METRO_STORE_QUERY)
    except Exception as exc:
        job.status = MetroScrapeJob.Status.ERROR
        job.error = str(exc)
        job.save(update_fields=["status", "error"])
        messages.error(request, f"Scanarea automată nu a putut porni: {exc}")
    else:
        messages.success(request, "Catalogarea masivă rulează în fundal și importă incremental rezultatele.")
    return redirect("comparator:metro_scrape_detail", pk=job.pk)


def metro_scrape_breadth_start(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not settings.METRO_SCRAPER_ENABLED:
        raise PermissionDenied("Scanarea Selenium este dezactivată în acest mediu.")
    active = MetroScrapeJob.objects.filter(
        status__in=[MetroScrapeJob.Status.PENDING, MetroScrapeJob.Status.RUNNING]
    ).first()
    if active:
        messages.warning(request, "Există deja o scanare activă.")
        return redirect("comparator:metro_scrape_detail", pk=active.pk)
    job = MetroScrapeJob.objects.create(
        start_url=settings.METRO_START_URL,
        scan_type=MetroScrapeJob.ScanType.TARGETED,
    )
    try:
        launch_breadth_catalog_job(job, settings.METRO_STORE_QUERY)
    except Exception as exc:
        job.status = MetroScrapeJob.Status.ERROR
        job.error = str(exc)
        job.save(update_fields=["status", "error"])
        messages.error(request, f"Extinderea rapidă nu a putut porni: {exc}")
    else:
        messages.success(request, "Extinderea rapidă rulează în fundal și importă incremental rezultatele.")
    return redirect("comparator:metro_scrape_detail", pk=job.pk)


def metro_scrape_alphabet_start(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not settings.METRO_SCRAPER_ENABLED:
        raise PermissionDenied("Scanarea Selenium este dezactivată în acest mediu.")
    active = MetroScrapeJob.objects.filter(
        status__in=[MetroScrapeJob.Status.PENDING, MetroScrapeJob.Status.RUNNING]
    ).first()
    if active:
        messages.warning(request, "Există deja o scanare activă.")
        return redirect("comparator:metro_scrape_detail", pk=active.pk)
    job = MetroScrapeJob.objects.create(
        start_url=settings.METRO_START_URL,
        scan_type=MetroScrapeJob.ScanType.TARGETED,
    )
    try:
        launch_alphabet_catalog_job(job, settings.METRO_STORE_QUERY)
    except Exception as exc:
        job.status = MetroScrapeJob.Status.ERROR
        job.error = str(exc)
        job.save(update_fields=["status", "error"])
        messages.error(request, f"Scanarea alfabetică nu a putut porni: {exc}")
    else:
        messages.success(request, "Scanarea alfabetică rulează în fundal și importă incremental rezultatele.")
    return redirect("comparator:metro_scrape_detail", pk=job.pk)


def metro_sitemap_import(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        products = fetch_metro_sitemap_products()
        stats = import_metro_sitemap_products(products)
    except MetroSitemapError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Catalogul public METRO a fost sincronizat: {stats['new_products']} produse și "
            f"{stats['new_codes']} coduri noi din {stats['discovered']} descoperite.",
        )
    return redirect("comparator:metro_scrape_list")


def metro_scrape_detail(request, pk):
    job = get_object_or_404(MetroScrapeJob, pk=pk)
    rows = job.products.select_related("matched_product")
    page_obj = Paginator(rows, 100).get_page(request.GET.get("page"))
    return render(
        request,
        "comparator/metro_scrape_detail.html",
        {
            "job": job,
            "rows": page_obj,
            "page_obj": page_obj,
            "page_query": _page_query(request),
            "base_units": BaseUnit.choices,
            "search_terms": job.terms.all(),
        },
    )


def metro_scrape_import(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    job = get_object_or_404(MetroScrapeJob, pk=pk)
    selected_ids = request.POST.getlist("selected")
    if not selected_ids:
        messages.warning(request, "Nu ai selectat niciun produs pentru import.")
        return redirect("comparator:metro_scrape_detail", pk=pk)
    rows = job.products.filter(pk__in=selected_ids, imported=False)
    try:
        for row in rows:
            units = Decimal(request.POST[f"units_{row.pk}"].replace(",", "."))
            size = Decimal(request.POST[f"size_{row.pk}"].replace(",", "."))
            price = Decimal(request.POST[f"price_{row.pk}"].replace(",", "."))
            base_unit = request.POST[f"unit_{row.pk}"]
            if units <= 0 or size <= 0 or price < 0 or base_unit not in BaseUnit.values:
                raise ValueError(f"Valori invalide pentru {row.name}.")
            product_id = request.POST.get(f"product_{row.pk}")
            row.name = request.POST[f"name_{row.pk}"].strip()[:240]
            row.units_per_package = units
            row.unit_size = size
            row.base_unit = base_unit
            row.category = request.POST.get(f"category_{row.pk}", "").strip()[:80]
            row.price_gross = price
            row.matched_product = Product.objects.filter(pk=product_id).first() if product_id else None
            row.save(
                update_fields=[
                    "name", "units_per_package", "unit_size", "base_unit", "category", "price_gross", "matched_product"
                ]
            )
    except (KeyError, InvalidOperation, ValueError) as exc:
        messages.error(request, f"Importul nu a fost efectuat: {exc}")
        return redirect("comparator:metro_scrape_detail", pk=pk)
    count = import_scraped_rows(rows)
    messages.success(request, f"Au fost importate {count} prețuri METRO verificate.")
    return redirect("comparator:metro_scrape_detail", pk=pk)


def invoice_list(request):
    query = request.GET.get("q", "").strip()[:100]
    selected_status = request.GET.get("status", "").strip()
    selected_type = request.GET.get("type", "").strip()
    invoices = Invoice.objects.select_related("supplier").prefetch_related("lines", "processing_jobs").annotate(line_count=Count("lines"))
    if query:
        invoices = invoices.filter(Q(supplier__name__icontains=query) | Q(number__icontains=query))
    if selected_status in Invoice.Status.values:
        invoices = invoices.filter(status=selected_status)
    else:
        selected_status = ""
    if selected_type in Invoice.DocumentType.values:
        invoices = invoices.filter(document_type=selected_type)
    else:
        selected_type = ""
    invoices = invoices.order_by("-issued_at", "-created_at")
    page_obj = Paginator(invoices, 100).get_page(request.GET.get("page"))
    return render(
        request,
        "comparator/invoice_list.html",
        {
            "invoices": page_obj,
            "page_obj": page_obj,
            "page_query": _page_query(request),
            "query": query,
            "selected_status": selected_status,
            "selected_type": selected_type,
            "status_choices": Invoice.Status.choices,
            "type_choices": Invoice.DocumentType.choices,
        },
    )


def document_inbox(request):
    jobs = DocumentProcessingJob.objects.select_related("invoice", "invoice__supplier", "requested_by")
    page_obj = Paginator(jobs, 100).get_page(request.GET.get("page"))
    return render(
        request,
        "comparator/document_inbox.html",
        {
            "jobs": page_obj,
            "page_obj": page_obj,
            "page_query": _page_query(request),
            "active_count": jobs.filter(status__in=[
                DocumentProcessingJob.Status.PENDING,
                DocumentProcessingJob.Status.RUNNING,
            ]).count(),
        },
    )


def invoice_create(request):
    form = InvoiceForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        invoice = form.save()
        for order, upload in enumerate(form.cleaned_data["documents"], start=1):
            DocumentPage.objects.create(invoice=invoice, file=upload, page_order=order)
        has_files = invoice.document or invoice.pages.exists()
        if form.cleaned_data["process_now"] and (has_files or invoice.ocr_text.strip()):
            enqueue_document(invoice, requested_by=request.user)
            messages.success(request, "Documentul a fost salvat și adăugat în coada locală de procesare.")
        else:
            messages.success(request, "Factura a fost salvată. Poți adăuga liniile manual.")
        return redirect("comparator:invoice_detail", pk=invoice.pk)
    return render(request, "comparator/invoice_form.html", {"form": form})


def invoice_detail(request, pk):
    invoice = get_object_or_404(
        Invoice.objects.select_related("supplier").prefetch_related("lines", "processing_jobs"),
        pk=pk,
    )
    rows = [
        (line, line.comparison())
        for line in invoice.lines.select_related("matched_product").prefetch_related(
            "matched_product__metro_offers__volume_tiers"
        )
    ]
    formset = InvoiceLineFormSet(queryset=invoice.lines.select_related("matched_product"), prefix="lines")
    return render(
        request,
        "comparator/invoice_detail.html",
        {
            "invoice": invoice,
            "rows": rows,
            "line_formset": formset,
            "page_upload_form": DocumentPagesForm(),
            "revisions": invoice.revisions.select_related("created_by")[:10],
            "processing_job": invoice.processing_jobs.first(),
        },
    )


def invoice_edit(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    form = InvoiceEditForm(request.POST or None, instance=invoice)
    if form.is_valid():
        invoice = form.save()
        sync_invoice_stock(invoice)
        messages.success(request, "Datele documentului au fost actualizate.")
        return redirect("comparator:invoice_detail", pk=invoice.pk)
    return render(request, "comparator/form.html", {"form": form, "title": "Editează documentul"})


def invoice_delete(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("supplier").prefetch_related("pages"), pk=pk)
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.method == "POST":
        if request.POST.get("confirmation", "").strip().upper() != "STERGE":
            return render(
                request,
                "comparator/invoice_confirm_delete.html",
                {"invoice": invoice, "confirmation_error": True},
                status=400,
            )
        delete_invoice(invoice)
        messages.success(request, "Documentul și fișierele lui au fost șterse.")
        return redirect("comparator:invoice_list")
    return render(request, "comparator/invoice_confirm_delete.html", {"invoice": invoice})


def _private_file_response(field_file):
    if not field_file or not field_file.name:
        raise Http404
    try:
        response = FileResponse(field_file.open("rb"), as_attachment=True, filename=Path(field_file.name).name)
    except FileNotFoundError as exc:
        raise Http404 from exc
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def invoice_file_download(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    return _private_file_response(invoice.document)


def document_page_download(request, pk):
    page = get_object_or_404(DocumentPage, pk=pk)
    return _private_file_response(page.file)


def invoice_pages_add(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invoice = get_object_or_404(Invoice, pk=pk)
    form = DocumentPagesForm(request.POST, request.FILES)
    if not form.is_valid():
        errors = " ".join(error for field_errors in form.errors.values() for error in field_errors)
        messages.error(request, errors or "Fișierele nu sunt valide.")
        return redirect("comparator:invoice_detail", pk=pk)
    try:
        added = add_document_pages(invoice, form.cleaned_data["documents"])
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"Au fost adăugate {added} fișiere. Verifică ordinea și reprocesează OCR.")
    return redirect("comparator:invoice_detail", pk=pk)


def document_page_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    page = get_object_or_404(DocumentPage, pk=pk)
    invoice_id = delete_document_page(page)
    messages.success(request, "Fișierul a fost șters. Reprocesează OCR când ordinea este corectă.")
    return redirect("comparator:invoice_detail", pk=invoice_id)


def document_page_move(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    page = get_object_or_404(DocumentPage, pk=pk)
    try:
        invoice_id, moved = move_document_page(page, request.POST.get("direction", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("comparator:invoice_detail", pk=page.invoice_id)
    if moved:
        messages.success(request, "Ordinea fișierelor a fost actualizată.")
    return redirect("comparator:invoice_detail", pk=invoice_id)


def invoice_process(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invoice = get_object_or_404(Invoice, pk=pk)
    if invoice.lines.exists() and request.POST.get("confirm_replace") != "1":
        return render(request, "comparator/invoice_confirm_reprocess.html", {"invoice": invoice})
    job, created = enqueue_document(
        invoice,
        force_ocr=bool(invoice.document or invoice.pages.exists()),
        requested_by=request.user,
    )
    if created:
        messages.success(request, "Documentul a fost adăugat în coada locală pentru reprocesare.")
    else:
        messages.info(request, f"Documentul are deja un job activ: #{job.pk}.")
    return redirect("comparator:invoice_detail", pk=pk)


def invoice_revision_restore(request, pk, revision_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    revision = get_object_or_404(InvoiceRevision, pk=revision_pk, invoice_id=pk)
    try:
        restore_invoice_revision(revision, created_by=request.user)
    except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
        messages.error(request, f"Versiunea nu a putut fi restaurată: {exc}")
    else:
        messages.success(request, f"Au fost restaurate {revision.line_count} linii din versiunea aleasă.")
    return redirect("comparator:invoice_detail", pk=pk)


def _save_line(form, invoice=None):
    previous = None
    if form.instance.pk:
        previous = InvoiceLine.objects.filter(pk=form.instance.pk).values(
            "matched_product_id", "needs_review"
        ).first()
    user_confirmed = not form.cleaned_data.get("needs_review", True)
    line = form.save(commit=False)
    if invoice:
        line.invoice = invoice
    if line.matched_product_id:
        best_candidate_id = (
            line.match_candidates[0].get("product_id")
            if line.match_candidates
            else None
        )
        if best_candidate_id != line.matched_product_id:
            line.match_score = 100
            line.match_gap = 100
            line.match_method = InvoiceLine.MatchMethod.MANUAL
            line.match_corrected = bool(best_candidate_id)
    else:
        apply_match(line)
    if user_confirmed:
        line.needs_review = False
    if line.invoice.supplier.is_metro and not line.needs_review and not line.matched_product_id:
        line.matched_product, _ = Product.objects.get_or_create(
            name=line.original_name.strip(),
            brand="",
            base_unit=line.base_unit,
        )
        line.match_score = 100
        line.match_gap = 100
        line.match_method = InvoiceLine.MatchMethod.MANUAL
    line.save()
    metro_offer = sync_metro_offer_from_line(line)
    if previous and not previous["needs_review"] and (
        previous["matched_product_id"] != line.matched_product_id or line.needs_review
    ):
        reconcile_derived_metro_offer(line.invoice, previous["matched_product_id"])
    sync_supplier_offer_from_line(line)
    sync_stock_from_line(line)
    if line.matched_product_id and not line.needs_review:
        ProductAlias.objects.update_or_create(
            supplier=line.invoice.supplier,
            alias=line.original_name.strip(),
            defaults={"product": line.matched_product},
        )
    if line.invoice.lines.filter(needs_review=True).exists():
        line.invoice.status = Invoice.Status.REVIEW
    else:
        line.invoice.status = Invoice.Status.PROCESSED
    line.invoice.save(update_fields=["status"])
    refresh_supplier_profile_metrics(line.invoice.supplier)
    return line, metro_offer


@transaction.atomic
def invoice_lines_review(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invoice = get_object_or_404(Invoice, pk=pk)
    formset = InvoiceLineFormSet(request.POST, queryset=invoice.lines.all(), prefix="lines")
    if not formset.is_valid():
        rows = [
            (line, line.comparison())
            for line in invoice.lines.select_related("matched_product").prefetch_related(
                "matched_product__metro_offers__volume_tiers"
            )
        ]
        messages.error(request, "Unele valori nu sunt valide. Corectează câmpurile marcate.")
        return render(
            request,
            "comparator/invoice_detail.html",
            {
                "invoice": invoice,
                "rows": rows,
                "line_formset": formset,
                "page_upload_form": DocumentPagesForm(),
                "revisions": invoice.revisions.select_related("created_by")[:10],
            },
            status=400,
        )
    saved = 0
    for form in formset:
        if not form.instance.pk:
            continue
        if form.cleaned_data.get("DELETE"):
            delete_invoice_line(form.instance)
            continue
        _save_line(form)
        saved += 1
    messages.success(request, f"Au fost verificate și salvate {saved} linii.")
    return redirect("comparator:invoice_detail", pk=invoice.pk)


def barcode_scanner(request):
    selected_line = None
    selected_product = None
    line_id = request.GET.get("line", "")
    product_id = request.GET.get("product", "")
    if line_id.isdigit():
        selected_line = get_object_or_404(
            InvoiceLine.objects.select_related("matched_product", "invoice"),
            pk=line_id,
        )
        selected_product = selected_line.matched_product
    elif product_id.isdigit():
        selected_product = get_object_or_404(Product, pk=product_id, active=True)
    return render(
        request,
        "comparator/barcode_scanner.html",
        {"selected_product": selected_product, "selected_line": selected_line},
    )


def barcode_lookup(request):
    code = normalize_barcode(request.GET.get("code"))
    if not code:
        return JsonResponse({"found": False, "error": "Cod lipsă."}, status=400)
    product = Product.objects.filter(ean=code, active=True).first()
    if not product:
        code_row = ProductCode.objects.select_related("product").filter(
            kind=ProductCode.Kind.EAN, code=code, supplier__isnull=True, product__active=True
        ).first()
        product = code_row.product if code_row else None
    if not product:
        return JsonResponse({"found": False, "code": code})
    return JsonResponse(
        {"found": True, "code": code, "product": {"id": product.pk, "name": product.name, "brand": product.brand}}
    )


def barcode_assign(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    code = normalize_barcode(request.POST.get("code"))
    product_id = request.POST.get("product", "").strip()
    line_id = request.POST.get("line", "").strip()
    product = Product.objects.filter(pk=product_id, active=True).first() if product_id.isdigit() else None
    if not product:
        messages.error(request, "Alege produsul pentru acest cod de bare.")
        return redirect("comparator:barcode_scanner")
    if not is_valid_gtin(code):
        messages.error(request, "Cod EAN/GTIN invalid. Verifică scanarea.")
    else:
        try:
            assign_ean(product, code)
        except ValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"EAN {code} a fost asociat produsului {product.name}.")
            if line_id.isdigit():
                line = InvoiceLine.objects.select_related("invoice").filter(
                    pk=line_id,
                    matched_product=product,
                ).first()
                if line:
                    line.ean = code
                    line.save(update_fields=["ean"])
                    sync_supplier_offer_from_line(line)
                    messages.success(request, "Codul a fost memorat și pentru furnizorul documentului.")
                    return redirect("comparator:invoice_detail", pk=line.invoice_id)
            return redirect("comparator:product_detail", pk=product.pk)
    return redirect("comparator:barcode_scanner")


def line_create(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    form = InvoiceLineForm(request.POST or None)
    if form.is_valid():
        _, metro_offer = _save_line(form, invoice)
        message = "Linia a fost adăugată."
        if metro_offer:
            message += " Prețul de referință METRO a fost actualizat."
        messages.success(request, message)
        return redirect("comparator:invoice_detail", pk=pk)
    return render(request, "comparator/form.html", {"form": form, "title": "Linie de produs nouă"})


def line_edit(request, pk):
    line = get_object_or_404(InvoiceLine, pk=pk)
    form = InvoiceLineForm(request.POST or None, instance=line)
    if form.is_valid():
        _, metro_offer = _save_line(form)
        message = "Linia a fost actualizată, iar asocierea va fi memorată."
        if metro_offer:
            message += " Prețul de referință METRO a fost actualizat automat."
        messages.success(request, message)
        return redirect("comparator:invoice_detail", pk=line.invoice_id)
    return render(request, "comparator/form.html", {"form": form, "title": "Corectează linia"})


def line_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    line = get_object_or_404(InvoiceLine, pk=pk)
    invoice_id = line.invoice_id
    delete_invoice_line(line)
    messages.success(request, "Linia a fost ștearsă.")
    return redirect("comparator:invoice_detail", pk=invoice_id)

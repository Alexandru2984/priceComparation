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
from django.db.models import Count, Max, Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    InvoiceForm,
    InvoiceEditForm,
    DocumentPagesForm,
    InvoiceLineForm,
    InvoiceLineFormSet,
    MetroImportForm,
    MetroOfferForm,
    PriceAlertForm,
    ProductForm,
    ShoppingListForm,
    ShoppingListItemForm,
    SupplierForm,
)
from .models import (
    BaseUnit,
    DocumentPage,
    Invoice,
    InvoiceLine,
    InvoiceRevision,
    MetroOffer,
    MetroOfferTier,
    MetroProductState,
    MetroScrapeJob,
    PriceAlert,
    Product,
    ProductCode,
    ProductAlias,
    PushSubscription,
    ShoppingList,
    ShoppingListItem,
    Supplier,
)
from .services.barcodes import assign_ean, is_valid_gtin, normalize_barcode
from .services.documents import add_document_pages, delete_document_page, move_document_page
from .services.invoices import (
    delete_invoice,
    delete_invoice_line,
    process_invoice,
    reconcile_derived_metro_offer,
    restore_invoice_revision,
    sync_all_confirmed_metro_lines,
    sync_metro_offer_from_line,
    sync_supplier_offer_from_line,
)
from .services.exports import build_catalog_csv, build_catalog_xlsx
from .services.matching import apply_match
from .services.metro_scraper import import_scraped_rows, launch_mass_catalog_job, launch_scrape_job
from .services.insights import (
    catalog_quality_summary,
    product_history,
    recent_metro_changes,
    shopping_recommendation,
)
from .services.notifications import is_allowed_push_endpoint, send_to_active_staff, webpush_configured


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
    return render(
        request,
        "comparator/product_detail.html",
        {"product": product, "history": history, "minimum": minimum, "maximum": maximum},
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
            defaults={"quantity": item.quantity, "purchased": item.purchased},
        )
        messages.success(request, "Produsul a fost adăugat în listă.")
        return redirect("comparator:shopping_list_detail", pk=pk)
    rows = [(item, shopping_recommendation(item)) for item in shopping_list.items.select_related("product")]
    estimated_total = sum((result["total"] or Decimal("0") for _, result in rows), Decimal("0"))
    potential_saving = sum((result["saving"] or Decimal("0") for _, result in rows), Decimal("0"))
    return render(
        request,
        "comparator/shopping_list_detail.html",
        {
            "shopping_list": shopping_list,
            "rows": rows,
            "form": form,
            "estimated_total": estimated_total,
            "potential_saving": potential_saving,
        },
    )


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


def metro_list(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    availability = request.GET.get("availability", "active").strip()
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
    page_obj = Paginator(offers, 100).get_page(request.GET.get("page"))
    confirmed_document_lines = InvoiceLine.objects.filter(
        invoice__supplier__is_metro=True, needs_review=False, matched_product__isnull=False
    ).count()
    tracked_store = settings.PREFERRED_METRO_STORE.strip()
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
            "categories": Product.objects.exclude(category="").values_list("category", flat=True).distinct().order_by("category"),
            "confirmed_document_lines": confirmed_document_lines,
            "preferred_metro_store": settings.PREFERRED_METRO_STORE,
            "freshness": freshness,
        },
    )


def metro_offer_create(request):
    form = MetroOfferForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Prețul METRO a fost salvat.")
        return redirect("comparator:metro_list")
    return render(request, "comparator/form.html", {"form": form, "title": "Preț METRO nou"})


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
    return render(request, "comparator/metro_scrape_list.html", {"jobs": jobs})


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
    job = MetroScrapeJob.objects.create(start_url=settings.METRO_START_URL)
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
    invoices = Invoice.objects.select_related("supplier").prefetch_related("lines").annotate(line_count=Count("lines"))
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


def invoice_create(request):
    form = InvoiceForm(request.POST or None, request.FILES or None)
    if form.is_valid():
        invoice = form.save()
        for order, upload in enumerate(form.cleaned_data["documents"], start=1):
            DocumentPage.objects.create(invoice=invoice, file=upload, page_order=order)
        has_files = invoice.document or invoice.pages.exists()
        if form.cleaned_data["process_now"] and (has_files or invoice.ocr_text.strip()):
            try:
                process_invoice(invoice)
                messages.success(request, "Factura a fost procesată local.")
            except Exception as exc:
                invoice.status = Invoice.Status.ERROR
                invoice.processing_error = str(exc)
                invoice.save(update_fields=["status", "processing_error"])
                messages.warning(request, f"Factura a fost salvată, dar procesarea a eșuat: {exc}")
        else:
            messages.success(request, "Factura a fost salvată. Poți adăuga liniile manual.")
        return redirect("comparator:invoice_detail", pk=invoice.pk)
    return render(request, "comparator/invoice_form.html", {"form": form})


def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.select_related("supplier").prefetch_related("lines"), pk=pk)
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
        },
    )


def invoice_edit(request, pk):
    invoice = get_object_or_404(Invoice, pk=pk)
    form = InvoiceEditForm(request.POST or None, instance=invoice)
    if form.is_valid():
        form.save()
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
    try:
        process_invoice(
            invoice,
            force_ocr=bool(invoice.document or invoice.pages.exists()),
            created_by=request.user,
        )
        messages.success(request, "Factura a fost reprocesată local.")
    except Exception as exc:
        invoice.status = Invoice.Status.ERROR
        invoice.processing_error = str(exc)
        invoice.save(update_fields=["status", "processing_error"])
        messages.error(request, str(exc))
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
        line.match_score = 100
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
    line.save()
    metro_offer = sync_metro_offer_from_line(line)
    if previous and not previous["needs_review"] and (
        previous["matched_product_id"] != line.matched_product_id or line.needs_review
    ):
        reconcile_derived_metro_offer(line.invoice, previous["matched_product_id"])
    sync_supplier_offer_from_line(line)
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
    return render(request, "comparator/barcode_scanner.html")


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

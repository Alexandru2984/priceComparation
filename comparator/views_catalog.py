from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .forms import PriceAlertForm, ProductForm
from .models import InventoryItem, InvoiceLine, MetroOffer, PriceAlert, Product, ProductCode
from .services.barcodes import assign_ean, is_valid_gtin, normalize_barcode
from .services.exports import build_catalog_csv, build_catalog_xlsx
from .services.insights import (
    current_metro_offers_prefetch,
    product_history,
    profitability_analysis,
)
from .services.inventory import inventory_with_balance
from .services.invoices import sync_supplier_offer_from_line


def _page_query(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def _filtered_products(request):
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    products = Product.objects.prefetch_related(current_metro_offers_prefetch())
    if query:
        products = products.filter(Q(name__icontains=query) | Q(brand__icontains=query) | Q(ean__icontains=query))
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
            "categories": Product.objects.exclude(category="")
            .values_list("category", flat=True)
            .distinct()
            .order_by("category"),
            "preferred_metro_store": settings.PREFERRED_METRO_STORE,
        },
    )


def product_search(request):
    query = request.GET.get("q", "").strip()[:100]
    if len(query) < 2:
        return JsonResponse({"products": []})
    products = (
        Product.objects.filter(active=True)
        .filter(
            Q(name__icontains=query)
            | Q(brand__icontains=query)
            | Q(ean__icontains=query)
            | Q(codes__code__icontains=query)
        )
        .distinct()
        .order_by("name", "brand")[:20]
    )
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
    if request.method == "POST" and not request.user.is_superuser:
        raise PermissionDenied("Configurarea alertelor necesită rolul de administrator PriceMatch.")
    form = PriceAlertForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Alerta de preț a fost salvată.")
        return redirect("comparator:price_alert_list")
    alerts = PriceAlert.objects.select_related("product").prefetch_related(
        current_metro_offers_prefetch("product__metro_offers")
    )
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
        code_row = (
            ProductCode.objects.select_related("product")
            .filter(kind=ProductCode.Kind.EAN, code=code, supplier__isnull=True, product__active=True)
            .first()
        )
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
                line = (
                    InvoiceLine.objects.select_related("invoice")
                    .filter(
                        pk=line_id,
                        matched_product=product,
                    )
                    .first()
                )
                if line:
                    line.ean = code
                    line.save(update_fields=["ean"])
                    sync_supplier_offer_from_line(line)
                    messages.success(request, "Codul a fost memorat și pentru furnizorul documentului.")
                    return redirect("comparator:invoice_detail", pk=line.invoice_id)
            return redirect("comparator:product_detail", pk=product.pk)
    return redirect("comparator:barcode_scanner")

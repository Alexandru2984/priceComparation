import csv
import io
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, Min, OuterRef, Q, Subquery
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import MetroImportForm, MetroOfferForm
from .models import (
    BaseUnit,
    InvoiceLine,
    MetroOffer,
    MetroOfferTier,
    MetroPriceAnomaly,
    MetroProductState,
    MetroScrapeJob,
    Product,
)
from .services.invoices import sync_all_confirmed_metro_lines
from .services.metro_scraper import (
    import_scraped_rows,
    launch_alphabet_catalog_job,
    launch_api_catalog_job,
    launch_breadth_catalog_job,
    launch_mass_catalog_job,
    launch_scrape_job,
)
from .services.metro_sitemap import (
    MetroSitemapError,
    fetch_metro_sitemap_products,
    import_metro_sitemap_products,
)


def _page_query(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


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
        offers = offers.filter(pk=Subquery(latest_offer.order_by("-valid_from", "-pk").values("pk")[:1]))
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
            "categories": Product.objects.exclude(category="")
            .values_list("category", flat=True)
            .distinct()
            .order_by("category"),
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
            raise ValueError(f"Linia {row_number}: completează împreună volume_min_packages și volume_price_gross.")
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
        {
            "jobs": jobs,
            "preferred_metro_store": settings.PREFERRED_METRO_STORE,
            "metro_api_enabled": settings.METRO_API_ENABLED,
            "metro_selenium_enabled": settings.METRO_SELENIUM_ENABLED,
        },
    )


def metro_scrape_start(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not settings.METRO_SELENIUM_ENABLED:
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
    if not settings.METRO_SELENIUM_ENABLED:
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


def metro_scrape_api_start(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not settings.METRO_API_ENABLED:
        raise PermissionDenied("Scanarea API METRO este dezactivată în acest mediu.")
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
        launch_api_catalog_job(job, settings.METRO_STORE_QUERY)
    except Exception as exc:
        job.status = MetroScrapeJob.Status.ERROR
        job.error = str(exc)
        job.save(update_fields=["status", "error"])
        messages.error(request, f"Scanarea API nu a putut porni: {exc}")
    else:
        messages.success(request, "Catalogul METRO cu prețuri se actualizează rapid în fundal.")
    return redirect("comparator:metro_scrape_detail", pk=job.pk)


def metro_scrape_breadth_start(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if not settings.METRO_SELENIUM_ENABLED:
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
    if not settings.METRO_SELENIUM_ENABLED:
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
                    "name",
                    "units_per_package",
                    "unit_size",
                    "base_unit",
                    "category",
                    "price_gross",
                    "matched_product",
                ]
            )
    except (KeyError, InvalidOperation, ValueError) as exc:
        messages.error(request, f"Importul nu a fost efectuat: {exc}")
        return redirect("comparator:metro_scrape_detail", pk=pk)
    count = import_scraped_rows(rows)
    messages.success(request, f"Au fost importate {count} prețuri METRO verificate.")
    return redirect("comparator:metro_scrape_detail", pk=pk)

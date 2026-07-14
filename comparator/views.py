import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.contrib import messages
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    InvoiceForm,
    InvoiceLineForm,
    MetroImportForm,
    MetroOfferForm,
    ProductForm,
    SupplierForm,
)
from .models import (
    BaseUnit,
    DocumentPage,
    Invoice,
    InvoiceLine,
    MetroOffer,
    MetroScrapeJob,
    Product,
    ProductAlias,
    Supplier,
)
from .services.invoices import process_invoice, sync_all_confirmed_metro_lines, sync_metro_offer_from_line
from .services.matching import apply_match
from .services.metro_scraper import import_scraped_rows, launch_scrape_job


def dashboard(request):
    recent_invoices = Invoice.objects.select_related("supplier")[:6]
    review_count = InvoiceLine.objects.filter(needs_review=True).count()
    comparisons = []
    for line in InvoiceLine.objects.select_related("invoice", "invoice__supplier", "matched_product"):
        comparison = line.comparison()
        if comparison:
            comparisons.append((line, comparison))
    comparisons.sort(key=lambda item: abs(item[1]["total_impact"]), reverse=True)
    return render(
        request,
        "comparator/dashboard.html",
        {
            "recent_invoices": recent_invoices,
            "review_count": review_count,
            "product_count": Product.objects.filter(active=True).count(),
            "offer_count": MetroOffer.objects.filter(active=True).count(),
            "comparisons": comparisons[:8],
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


def product_list(request):
    query = request.GET.get("q", "").strip()
    products = Product.objects.prefetch_related("metro_offers")
    if query:
        products = products.filter(name__icontains=query)
    rows = []
    for product in products:
        offer = product.current_metro_offer()
        rows.append((product, offer))
    return render(request, "comparator/product_list.html", {"rows": rows, "query": query})


def product_create(request):
    form = ProductForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Produsul a fost adăugat în catalogul urmărit.")
        return redirect("comparator:product_list")
    return render(request, "comparator/form.html", {"form": form, "title": "Produs urmărit nou"})


def metro_list(request):
    offers = MetroOffer.objects.select_related("product")
    confirmed_document_lines = InvoiceLine.objects.filter(
        invoice__supplier__is_metro=True, needs_review=False, matched_product__isnull=False
    ).count()
    return render(
        request,
        "comparator/metro_list.html",
        {"offers": offers, "confirmed_document_lines": confirmed_document_lines},
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
        MetroOffer.objects.create(
            product=product,
            units_per_package=_decimal(row.get("units_per_package")),
            unit_size=_decimal(row.get("unit_size")),
            price_gross=_decimal(row.get("price_gross"), "0"),
            valid_from=valid_from,
            source=(row.get("source") or "METRO").strip(),
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


def metro_scrape_detail(request, pk):
    job = get_object_or_404(MetroScrapeJob, pk=pk)
    rows = job.products.select_related("matched_product")
    products = Product.objects.filter(active=True)
    return render(
        request,
        "comparator/metro_scrape_detail.html",
        {"job": job, "rows": rows, "catalog_products": products, "base_units": BaseUnit.choices},
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
            row.price_gross = price
            row.matched_product = Product.objects.filter(pk=product_id).first() if product_id else None
            row.save(
                update_fields=[
                    "name", "units_per_package", "unit_size", "base_unit", "price_gross", "matched_product"
                ]
            )
    except (KeyError, InvalidOperation, ValueError) as exc:
        messages.error(request, f"Importul nu a fost efectuat: {exc}")
        return redirect("comparator:metro_scrape_detail", pk=pk)
    count = import_scraped_rows(rows)
    messages.success(request, f"Au fost importate {count} prețuri METRO verificate.")
    return redirect("comparator:metro_scrape_detail", pk=pk)


def invoice_list(request):
    invoices = Invoice.objects.select_related("supplier").annotate(line_count=Count("lines"))
    return render(request, "comparator/invoice_list.html", {"invoices": invoices})


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
    invoice = get_object_or_404(Invoice.objects.select_related("supplier"), pk=pk)
    rows = [(line, line.comparison()) for line in invoice.lines.select_related("matched_product")]
    return render(request, "comparator/invoice_detail.html", {"invoice": invoice, "rows": rows})


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


def invoice_process(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invoice = get_object_or_404(Invoice, pk=pk)
    try:
        process_invoice(invoice, force_ocr=bool(invoice.document or invoice.pages.exists()))
        messages.success(request, "Factura a fost reprocesată local.")
    except Exception as exc:
        invoice.status = Invoice.Status.ERROR
        invoice.processing_error = str(exc)
        invoice.save(update_fields=["status", "processing_error"])
        messages.error(request, str(exc))
    return redirect("comparator:invoice_detail", pk=pk)


def _save_line(form, invoice=None):
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
    line.delete()
    messages.success(request, "Linia a fost ștearsă.")
    return redirect("comparator:invoice_detail", pk=invoice_id)

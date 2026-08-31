from datetime import date
from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    InitialDataImportForm,
    SupplierForm,
    SupplierParsingProfileForm,
    SupplierPriceListUploadForm,
)
from .models import InitialDataImport, Supplier, SupplierPriceImport
from .services.initial_import import apply_initial_import, build_initial_workbook_template, parse_initial_workbook
from .services.price_lists import create_price_list_invoice, parse_supplier_price_list
from .services.supplier_profiles import refresh_supplier_profile_metrics


def supplier_list(request):
    suppliers = Supplier.objects.annotate(invoice_count=Count("invoices"))
    return render(request, "comparator/supplier_list.html", {"suppliers": suppliers})


def initial_import_template(request):
    response = HttpResponse(
        build_initial_workbook_template(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="pricematch-import-initial.xlsx"'
    return response


def initial_import_create(request):
    form = InitialDataImportForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        try:
            file_hash, rows = parse_initial_workbook(upload)
        except (OSError, ValueError) as exc:
            form.add_error("file", str(exc))
        else:
            existing = InitialDataImport.objects.filter(file_hash=file_hash).first()
            if existing:
                messages.info(request, "Același fișier a fost încărcat deja; am deschis importul existent.")
                return redirect("comparator:initial_import_detail", pk=existing.pk)
            initial_import = InitialDataImport.objects.create(
                original_filename=Path(upload.name).name[:255],
                file_hash=file_hash,
                rows=rows,
                row_count=len(rows),
                warning_count=sum(bool(row["errors"]) for row in rows),
                created_by=request.user,
            )
            return redirect("comparator:initial_import_detail", pk=initial_import.pk)
    return render(
        request,
        "comparator/initial_import_create.html",
        {"form": form, "recent_imports": InitialDataImport.objects.select_related("created_by")[:20]},
    )


def initial_import_detail(request, pk):
    initial_import = get_object_or_404(InitialDataImport.objects.select_related("created_by"), pk=pk)
    return render(
        request,
        "comparator/initial_import_detail.html",
        {
            "initial_import": initial_import,
            "supplier_rows": [row for row in initial_import.rows if row["kind"] == "SUPPLIER"],
            "product_rows": [row for row in initial_import.rows if row["kind"] == "PRODUCT"],
            "stock_rows": [row for row in initial_import.rows if row["kind"] == "STOCK"],
        },
    )


def initial_import_confirm(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    initial_import = get_object_or_404(InitialDataImport, pk=pk)
    try:
        stats = apply_initial_import(initial_import)
    except (ValidationError, ValueError) as exc:
        messages.error(request, f"Importul nu a fost aplicat: {exc}")
    else:
        if stats.get("already_applied"):
            messages.info(request, "Importul fusese deja aplicat; stocul nu a fost dublat.")
        else:
            messages.success(
                request,
                "Import aplicat: "
                f"{stats['suppliers_created']} furnizori noi, {stats['products_created']} produse noi, "
                f"{stats['stock_policies']} politici de stoc și {stats['opening_movements']} stocuri inițiale.",
            )
    return redirect("comparator:initial_import_detail", pk=initial_import.pk)


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
    form = SupplierPriceListUploadForm(
        request.POST or None, request.FILES or None, initial={"effective_at": date.today()}
    )
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
        if created
        else "Lista fusese deja importată; am deschis documentul existent.",
    )
    return redirect("comparator:invoice_detail", pk=invoice.pk)

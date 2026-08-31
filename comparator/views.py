from decimal import Decimal
from pathlib import Path

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import FileResponse, Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    DocumentPagesForm,
    InvoiceEditForm,
    InvoiceForm,
    InvoiceLineForm,
    InvoiceLineFormSet,
)
from .models import (
    AutomationRun,
    DocumentPage,
    DocumentProcessingJob,
    InventoryItem,
    Invoice,
    InvoiceLine,
    InvoiceRevision,
    MetroOffer,
    MetroPriceAnomaly,
    PriceAlert,
    Product,
    ProductAlias,
    Supplier,
)
from .services.documents import add_document_pages, delete_document_page, move_document_page
from .services.insights import (
    catalog_quality_summary,
    current_metro_offers_prefetch,
    matching_quality_summary,
    recent_metro_changes,
)
from .services.inventory import (
    sync_invoice_stock,
    sync_stock_from_line,
)
from .services.invoices import (
    delete_invoice,
    delete_invoice_line,
    prime_invoice_merchandise_totals,
    reconcile_derived_metro_offer,
    restore_invoice_revision,
    sync_metro_offer_from_line,
    sync_supplier_offer_from_line,
)
from .services.matching import apply_match
from .services.processing_queue import enqueue_document
from .services.supplier_profiles import refresh_supplier_profile_metrics


def _page_query(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


def dashboard(request):
    recent_invoices = Invoice.objects.select_related("supplier")[:6]
    review_count = InvoiceLine.objects.filter(needs_review=True).count()
    comparisons = []
    comparison_lines = InvoiceLine.objects.select_related(
        "invoice", "invoice__supplier", "matched_product"
    ).prefetch_related(current_metro_offers_prefetch("matched_product__metro_offers"))
    for line in prime_invoice_merchandise_totals(comparison_lines):
        comparison = line.comparison()
        if comparison:
            comparisons.append((line, comparison))
    comparisons.sort(key=lambda item: abs(item[1]["total_impact"]), reverse=True)
    total_impact = sum((item[1]["total_impact"] for item in comparisons), Decimal("0"))
    alerts = [
        alert
        for alert in PriceAlert.objects.select_related("product")
        .prefetch_related(current_metro_offers_prefetch("product__metro_offers"))
        .filter(active=True)
        if alert.is_triggered
    ]
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
            "open_metro_anomalies": MetroPriceAnomaly.objects.filter(status=MetroPriceAnomaly.Status.OPEN).count(),
            "recent_automation": AutomationRun.objects.select_related("metro_job")[:5],
            "supplier_count": supplier_count,
            "document_count": document_count,
            "inventory_count": inventory_count,
        },
    )


def invoice_evaluation_toggle(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    invoice = get_object_or_404(Invoice, pk=pk)
    invoice.evaluation_sample = not invoice.evaluation_sample
    invoice.save(update_fields=["evaluation_sample"])
    if invoice.evaluation_sample:
        messages.success(request, "Documentul a fost adăugat în setul local de calibrare OCR.")
    else:
        messages.success(request, "Documentul a fost scos din setul de calibrare OCR.")
    return redirect("comparator:invoice_detail", pk=invoice.pk)


def invoice_list(request):
    query = request.GET.get("q", "").strip()[:100]
    selected_status = request.GET.get("status", "").strip()
    selected_type = request.GET.get("type", "").strip()
    invoices = Invoice.objects.select_related("supplier").prefetch_related(
        "lines",
        Prefetch(
            "processing_jobs",
            queryset=DocumentProcessingJob.objects.filter(
                status__in=[
                    DocumentProcessingJob.Status.PENDING,
                    DocumentProcessingJob.Status.RUNNING,
                ]
            ),
            to_attr="active_processing_jobs",
        ),
    )
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
    for invoice in page_obj.object_list:
        lines = list(invoice.lines.all())
        invoice.line_count = len(lines)
        invoice._prefetched_merchandise_total_gross = sum(
            (line.merchandise_total_gross for line in lines), Decimal("0")
        )
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
            "active_count": jobs.filter(
                status__in=[
                    DocumentProcessingJob.Status.PENDING,
                    DocumentProcessingJob.Status.RUNNING,
                ]
            ).count(),
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
        Invoice.objects.select_related("supplier"),
        pk=pk,
    )
    comparison_lines = list(
        invoice.lines.select_related("matched_product").prefetch_related(
            current_metro_offers_prefetch("matched_product__metro_offers")
        )
    )
    invoice._prefetched_merchandise_total_gross = sum(
        (line.merchandise_total_gross for line in comparison_lines), Decimal("0")
    )
    for line in comparison_lines:
        line.invoice = invoice
    rows = [(line, line.comparison()) for line in comparison_lines]
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
        previous = InvoiceLine.objects.filter(pk=form.instance.pk).values("matched_product_id", "needs_review").first()
    user_confirmed = not form.cleaned_data.get("needs_review", True)
    line = form.save(commit=False)
    if invoice:
        line.invoice = invoice
    if line.matched_product_id:
        best_candidate_id = line.match_candidates[0].get("product_id") if line.match_candidates else None
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
    if (
        previous
        and not previous["needs_review"]
        and (previous["matched_product_id"] != line.matched_product_id or line.needs_review)
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

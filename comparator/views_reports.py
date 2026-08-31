from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .models import Invoice, InvoiceLine
from .services.evaluation import evaluate_confirmed_documents
from .services.insights import matching_quality_summary
from .services.weekly_reports import build_weekly_report, build_weekly_report_xlsx


def _page_query(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params.urlencode()


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


def ocr_evaluation(request):
    invoices = (
        Invoice.objects.filter(evaluation_sample=True)
        .select_related("supplier")
        .prefetch_related("lines", "pages")
        .order_by("-issued_at", "-created_at")
    )
    return render(
        request,
        "comparator/ocr_evaluation.html",
        {"report": evaluate_confirmed_documents(invoices)},
    )

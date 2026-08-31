from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from .forms import DataExportForm
from .models import ActivityLog, InventoryItem, Invoice, Product, Supplier
from .services.exports import build_complete_data_xlsx
from .services.health import system_readiness
from .services.operations import operation_summary


def activity_log(request):
    logs = ActivityLog.objects.select_related("user")
    actor = request.GET.get("actor", "").strip()
    outcome = request.GET.get("outcome", "").strip().upper()
    query = request.GET.get("q", "").strip()
    if actor:
        logs = logs.filter(user__username__icontains=actor)
    if outcome in ActivityLog.Outcome.values:
        logs = logs.filter(outcome=outcome)
    else:
        outcome = ""
    if query:
        logs = logs.filter(Q(path__icontains=query) | Q(view_name__icontains=query))
    page_obj = Paginator(logs, 100).get_page(request.GET.get("page"))
    page_params = request.GET.copy()
    page_params.pop("page", None)
    return render(
        request,
        "comparator/activity_log.html",
        {
            "page_obj": page_obj,
            "page_query": page_params.urlencode(),
            "actor": actor,
            "outcome": outcome,
            "query": query,
        },
    )


def _export_counts():
    return {
        "products": Product.objects.count(),
        "documents": Invoice.objects.count(),
        "inventory": InventoryItem.objects.count(),
        "suppliers": Supplier.objects.count(),
    }


def data_export_index(request):
    return render(
        request,
        "comparator/data_export.html",
        {"form": DataExportForm(request.GET or None), "counts": _export_counts()},
    )


def data_export_download(request):
    form = DataExportForm(request.GET)
    if not form.is_valid():
        return render(
            request,
            "comparator/data_export.html",
            {"form": form, "counts": _export_counts()},
            status=400,
        )
    content = build_complete_data_xlsx(**form.cleaned_data)
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="pricematch-date-{timezone.localdate().isoformat()}.xlsx"'
    return response


def readiness(request):
    return render(
        request,
        "comparator/readiness.html",
        {"report": system_readiness()},
    )


def operations(request):
    return render(
        request,
        "comparator/operations.html",
        {"summary": operation_summary()},
    )

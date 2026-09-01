from decimal import Decimal

from django.shortcuts import render

from .models import (
    AutomationRun,
    InventoryItem,
    Invoice,
    InvoiceLine,
    MetroOffer,
    MetroPriceAnomaly,
    PriceAlert,
    Product,
    Supplier,
)
from .services.insights import (
    catalog_quality_summary,
    current_metro_offers_prefetch,
    matching_quality_summary,
    recent_metro_changes,
)
from .services.invoices import (
    prime_invoice_merchandise_totals,
)


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

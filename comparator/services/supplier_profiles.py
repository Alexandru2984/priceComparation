from django.db.models import Avg, Count, Q

from comparator.models import InvoiceLine, SupplierParsingProfile


def get_supplier_profile(supplier):
    profile, _ = SupplierParsingProfile.objects.get_or_create(supplier=supplier)
    return profile


def refresh_supplier_profile_metrics(supplier):
    profile = get_supplier_profile(supplier)
    metrics = InvoiceLine.objects.filter(invoice__supplier=supplier).aggregate(
        confirmed=Count("id", filter=Q(needs_review=False)),
        corrected=Count("id", filter=Q(needs_review=False, match_corrected=True)),
        average=Avg("match_score", filter=Q(needs_review=False)),
    )
    profile.confirmed_lines = metrics["confirmed"] or 0
    profile.corrected_lines = metrics["corrected"] or 0
    profile.average_match_score = round(metrics["average"] or 0)
    profile.save(update_fields=[
        "confirmed_lines",
        "corrected_lines",
        "average_match_score",
        "updated_at",
    ])
    return profile

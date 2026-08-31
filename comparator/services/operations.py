from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Count
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from comparator.models import ActivityLog, DocumentProcessingJob, MetroScrapeJob


def _status_counts(queryset, values):
    counts = {value: 0 for value in values}
    for row in queryset.values("status").annotate(total=Count("id")):
        counts[row["status"]] = row["total"]
    return counts


def operation_summary():
    """Return aggregate operational data that is safe to display to an administrator."""
    users = get_user_model().objects.filter(is_staff=True)
    active_users = users.filter(is_active=True)
    since = timezone.now() - timedelta(hours=24)
    latest_document_job = DocumentProcessingJob.objects.first()
    latest_metro_job = MetroScrapeJob.objects.first()
    return {
        "environment": settings.DEPLOYMENT_ENVIRONMENT,
        "database": "PostgreSQL" if connection.vendor == "postgresql" else connection.vendor,
        "accounts": {
            "active": active_users.count(),
            "disabled": users.filter(is_active=False).count(),
            "admins": active_users.filter(is_superuser=True).count(),
            "operators": active_users.filter(is_superuser=False).count(),
            "mfa": TOTPDevice.objects.filter(
                confirmed=True,
                user__in=active_users,
            )
            .values("user_id")
            .distinct()
            .count(),
        },
        "document_jobs": _status_counts(DocumentProcessingJob.objects.all(), DocumentProcessingJob.Status.values),
        "metro_jobs": _status_counts(MetroScrapeJob.objects.all(), MetroScrapeJob.Status.values),
        "activity": {
            "denied_24h": ActivityLog.objects.filter(outcome=ActivityLog.Outcome.DENIED, created_at__gte=since).count(),
            "errors_24h": ActivityLog.objects.filter(outcome=ActivityLog.Outcome.ERROR, created_at__gte=since).count(),
        },
        "latest_document_job": latest_document_job,
        "latest_metro_job": latest_metro_job,
        "configuration": {
            "mfa_required": settings.MFA_REQUIRED,
            "ollama_enabled": settings.OLLAMA_ENABLED,
            "ollama_model": settings.OLLAMA_MODEL,
            "ocr_language": settings.OCR_LANGUAGE,
            "metro_api_enabled": settings.METRO_API_ENABLED,
            "metro_selenium_enabled": settings.METRO_SELENIUM_ENABLED,
            "preferred_store": settings.PREFERRED_METRO_STORE or settings.METRO_STORE_QUERY or "neconfigurat",
            "activity_retention_days": settings.ACTIVITY_LOG_RETENTION_DAYS,
            "technical_retention_days": settings.TECHNICAL_DATA_RETENTION_DAYS,
        },
    }

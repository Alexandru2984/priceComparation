import json
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from comparator.models import (
    ActivityLog,
    AutomationRun,
    DocumentProcessingJob,
    InvoiceRevision,
    MetroScrapedProduct,
    MetroScrapeJob,
    MetroScrapeTerm,
)


class Command(BaseCommand):
    help = "Curăță date tehnice expirate; fără --confirm afișează numai simularea."

    def add_arguments(self, parser):
        parser.add_argument("--confirm", action="store_true")
        parser.add_argument(
            "--activity-days",
            type=int,
            default=settings.ACTIVITY_LOG_RETENTION_DAYS,
        )
        parser.add_argument(
            "--technical-days",
            type=int,
            default=settings.TECHNICAL_DATA_RETENTION_DAYS,
        )
        parser.add_argument(
            "--revision-limit",
            type=int,
            default=settings.INVOICE_REVISION_LIMIT,
        )

    def _revision_ids(self, limit):
        stale_ids = []
        invoice_ids = InvoiceRevision.objects.order_by().values_list("invoice_id", flat=True).distinct()
        for invoice_id in invoice_ids.iterator():
            stale_ids.extend(
                InvoiceRevision.objects.filter(invoice_id=invoice_id)
                .order_by("-created_at", "-id")
                .values_list("id", flat=True)[limit:]
            )
        return stale_ids

    def handle(self, *args, **options):
        if options["activity_days"] < 30:
            raise CommandError("--activity-days trebuie să fie cel puțin 30.")
        if options["technical_days"] < 7:
            raise CommandError("--technical-days trebuie să fie cel puțin 7.")
        if options["revision_limit"] < 1:
            raise CommandError("--revision-limit trebuie să fie cel puțin 1.")

        now = timezone.now()
        activity_cutoff = now - timedelta(days=options["activity_days"])
        technical_cutoff = now - timedelta(days=options["technical_days"])
        terminal_scrapes = MetroScrapeJob.objects.filter(
            status__in=[MetroScrapeJob.Status.COMPLETED, MetroScrapeJob.Status.ERROR],
            created_at__lt=technical_cutoff,
        )
        terminal_processing = DocumentProcessingJob.objects.filter(
            status__in=[DocumentProcessingJob.Status.COMPLETED, DocumentProcessingJob.Status.ERROR],
            created_at__lt=technical_cutoff,
        )
        terminal_automation = AutomationRun.objects.filter(
            status__in=[AutomationRun.Status.COMPLETED, AutomationRun.Status.ERROR],
            started_at__lt=activity_cutoff,
        )
        revision_ids = self._revision_ids(options["revision_limit"])
        targets = {
            "expired_sessions": Session.objects.filter(expire_date__lt=now),
            "activity_logs": ActivityLog.objects.filter(created_at__lt=activity_cutoff),
            "scraped_products": MetroScrapedProduct.objects.filter(job__in=terminal_scrapes),
            "scrape_terms": MetroScrapeTerm.objects.filter(job__in=terminal_scrapes),
            "processing_jobs": terminal_processing,
            "automation_runs": terminal_automation,
            "invoice_revisions": InvoiceRevision.objects.filter(id__in=revision_ids),
        }
        counts = {name: queryset.count() for name, queryset in targets.items()}
        counts["mode"] = "aplicat" if options["confirm"] else "simulare"

        if options["confirm"]:
            with transaction.atomic():
                for queryset in targets.values():
                    queryset.delete()

        self.stdout.write(json.dumps(counts, ensure_ascii=False, indent=2))
        if not options["confirm"]:
            self.stdout.write("Nicio dată nu a fost ștearsă. Repetă cu --confirm pentru aplicare.")

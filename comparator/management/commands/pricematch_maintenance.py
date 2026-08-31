from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from comparator.models import AutomationRun, InventoryItem, MetroScrapeJob, ShoppingListItem
from comparator.services.metro_scraper import launch_api_catalog_job, launch_targeted_catalog_job


class Command(BaseCommand):
    help = "Rulează backupul, alertele și actualizările METRO scadente, cu jurnal în baza de date."

    def add_arguments(self, parser):
        parser.add_argument("--skip-backup", action="store_true")
        parser.add_argument(
            "--scan-metro",
            action="store_true",
            help="Pornește forțat o scanare completă (compatibilitate cu vechea comandă).",
        )
        parser.add_argument(
            "--scheduled-metro",
            action="store_true",
            help="Pornește scanarea completă sau țintită numai dacă intervalul configurat a expirat.",
        )
        parser.add_argument("--skip-notifications", action="store_true")
        parser.add_argument("--skip-cleanup", action="store_true")
        parser.add_argument("--backup-output", type=Path, default=Path(settings.BASE_DIR) / "backups")
        parser.add_argument("--store", default=settings.METRO_STORE_QUERY)

    def _tracked_terms(self):
        inventory_names = InventoryItem.objects.filter(active=True, product__active=True).values_list(
            "product__name", flat=True
        )
        shopping_names = ShoppingListItem.objects.filter(
            purchased=False,
            shopping_list__archived=False,
            product__active=True,
        ).values_list("product__name", flat=True)
        return list(dict.fromkeys([*inventory_names, *shopping_names]))[
            : settings.METRO_TARGETED_SCAN_MAX_PRODUCTS
        ]

    def _launch_metro_if_due(self, store, force_full=False):
        active = MetroScrapeJob.objects.filter(
            status__in=[MetroScrapeJob.Status.PENDING, MetroScrapeJob.Status.RUNNING]
        ).first()
        if active:
            return None, f"scanarea METRO #{active.pk} este deja activă"

        now = timezone.now()
        full_cutoff = now - timedelta(days=settings.METRO_FULL_SCAN_INTERVAL_DAYS)
        recent_full = MetroScrapeJob.objects.filter(
            scan_type=MetroScrapeJob.ScanType.FULL,
            status=MetroScrapeJob.Status.COMPLETED,
            finished_at__gte=full_cutoff,
        ).exists()
        if force_full or not recent_full:
            if not settings.METRO_API_ENABLED:
                return None, "scanare completă omisă: API-ul METRO este dezactivat"
            job = MetroScrapeJob.objects.create(
                start_url=settings.METRO_START_URL,
                scan_type=MetroScrapeJob.ScanType.FULL,
            )
            launch_api_catalog_job(job, store)
            return job, f"scanare completă METRO #{job.pk} pornită"

        targeted_cutoff = now - timedelta(hours=settings.METRO_TARGETED_SCAN_INTERVAL_HOURS)
        recent_targeted = MetroScrapeJob.objects.filter(
            scan_type=MetroScrapeJob.ScanType.TARGETED,
            status=MetroScrapeJob.Status.COMPLETED,
            finished_at__gte=targeted_cutoff,
        ).exists()
        if recent_targeted:
            return None, "scanarea țintită METRO nu este încă scadentă"
        if not settings.METRO_SELENIUM_ENABLED:
            return None, "scanare țintită omisă: Selenium este dezactivat"
        terms = self._tracked_terms()
        if not terms:
            return None, "nu există produse urmărite pentru scanarea țintită"
        job = MetroScrapeJob.objects.create(
            start_url=settings.METRO_START_URL,
            scan_type=MetroScrapeJob.ScanType.TARGETED,
        )
        launch_targeted_catalog_job(job, terms, store)
        return job, f"scanare țintită METRO #{job.pk} pornită pentru {len(terms)} produse"

    def handle(self, *args, **options):
        run = AutomationRun.objects.create()
        actions = []
        try:
            if not options["skip_backup"]:
                call_command("backup_pricematch", output=options["backup_output"])
                actions.append("backup verificat creat")

            if not options["skip_cleanup"]:
                call_command("cleanup_pricematch", confirm=True, verbosity=0)
                actions.append("date tehnice expirate curățate")

            scan_requested = options["scan_metro"] or options["scheduled_metro"] or settings.METRO_AUTOMATION_ENABLED
            if scan_requested:
                if not settings.METRO_API_ENABLED and not settings.METRO_SELENIUM_ENABLED:
                    actions.append("scanare METRO omisă: API și Selenium dezactivate")
                else:
                    job, message = self._launch_metro_if_due(
                        options["store"],
                        force_full=options["scan_metro"],
                    )
                    run.metro_job = job
                    actions.append(message)

            if not options["skip_notifications"] and settings.WEBPUSH_VAPID_PRIVATE_KEY:
                call_command("send_price_alerts")
                actions.append("alerte verificate")
            elif not options["skip_notifications"]:
                actions.append("alerte push neconfigurate")

            run.status = AutomationRun.Status.COMPLETED
            run.summary = "; ".join(actions) or "nicio operație solicitată"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "summary", "finished_at", "metro_job"])
        except Exception as exc:
            run.status = AutomationRun.Status.ERROR
            run.summary = str(exc)[:2000]
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "summary", "finished_at", "metro_job"])
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Mentenanță finalizată: {run.summary}."))

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Rulează operațiile periodice PriceMatch: backup și, opțional, actualizarea METRO."

    def add_arguments(self, parser):
        parser.add_argument("--skip-backup", action="store_true")
        parser.add_argument("--scan-metro", action="store_true")
        parser.add_argument("--backup-output", type=Path, default=Path(settings.BASE_DIR) / "backups")
        parser.add_argument("--store", default=settings.METRO_STORE_QUERY)

    def handle(self, *args, **options):
        if not options["skip_backup"]:
            call_command("backup_pricematch", output=options["backup_output"])
        if options["scan_metro"]:
            call_command("metro_seed_catalog", store=options["store"], delay=0.8, retries=3)
        self.stdout.write(self.style.SUCCESS("Mentenanța periodică s-a încheiat."))

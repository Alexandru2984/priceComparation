from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from comparator.services.weekly_reports import build_weekly_report, build_weekly_report_xlsx


class Command(BaseCommand):
    help = "Generează raportul operațional săptămânal în format XLSX."

    def add_arguments(self, parser):
        parser.add_argument("--end", help="Ultima zi inclusă, YYYY-MM-DD.")
        parser.add_argument("--output", type=Path, default=Path(settings.BASE_DIR) / "reports")

    def handle(self, *args, **options):
        try:
            end_date = date.fromisoformat(options["end"]) if options["end"] else timezone.localdate()
        except ValueError as exc:
            raise CommandError("--end trebuie să aibă formatul YYYY-MM-DD.") from exc
        output_directory = options["output"].resolve()
        output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        output_path = output_directory / f"raport-saptamanal-{end_date.isoformat()}.xlsx"
        output_path.write_bytes(build_weekly_report_xlsx(build_weekly_report(end_date)))
        output_path.chmod(0o600)
        self.stdout.write(str(output_path))

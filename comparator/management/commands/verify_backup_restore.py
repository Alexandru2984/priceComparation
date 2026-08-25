import os
import subprocess  # nosec B404
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Restaurează un backup într-o bază SQLite temporară și verifică aplicația fără a atinge datele curente."

    def add_arguments(self, parser):
        parser.add_argument("backup", type=Path)

    def handle(self, *args, **options):
        backup = options["backup"].expanduser().resolve()
        if not (backup / "manifest.json").is_file():
            raise CommandError("Directorul nu conține un backup PriceMatch valid.")

        with tempfile.TemporaryDirectory(prefix="pricematch-restore-check-") as directory:
            database = Path(directory) / "restore-check.sqlite3"
            environment = os.environ.copy()
            environment.update(
                {
                    "DB_ENGINE": "sqlite",
                    "SQLITE_PATH": str(database),
                    "DJANGO_DEBUG": "0",
                    "DJANGO_PRODUCTION": "0",
                    "DJANGO_TESTING": "1",
                    "MFA_REQUIRED": "0",
                }
            )
            base_command = [
                sys.executable,
                str(Path(settings.BASE_DIR) / "manage.py"),
            ]
            commands = [
                [*base_command, "migrate", "--noinput", "--verbosity", "0"],
                [
                    *base_command,
                    "restore_pricematch",
                    str(backup),
                    "--confirm",
                    "RESTORE",
                    "--without-media",
                    "--verbosity",
                    "0",
                ],
                [*base_command, "check", "--verbosity", "0"],
            ]
            for command in commands:
                result = subprocess.run(  # nosec B603
                    command,
                    cwd=settings.BASE_DIR,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
                if result.returncode:
                    detail = (result.stderr or result.stdout or "eroare necunoscută").strip()[-2000:]
                    raise CommandError(f"Testul de restaurare a eșuat: {detail}")

        self.stdout.write(self.style.SUCCESS("Backupul a fost restaurat și verificat într-o bază izolată."))

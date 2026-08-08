import gzip
import hashlib
import io
import json
import tarfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


EXCLUDED_MODELS = [
    "contenttypes",
    "auth.permission",
    "admin.logentry",
    "sessions",
    "axes",
]


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Command(BaseCommand):
    help = "Creează un backup portabil, comprimat și verificabil al bazei de date și fișierelor private."

    def add_arguments(self, parser):
        parser.add_argument("--output", type=Path, default=Path(settings.BASE_DIR) / "backups")
        parser.add_argument("--without-media", action="store_true")

    def handle(self, *args, **options):
        root = options["output"].expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        destination = root / f"pricematch-{timestamp}"
        try:
            destination.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise CommandError("Există deja un backup creat în aceeași secundă. Reîncearcă.") from exc

        buffer = io.StringIO()
        call_command("dumpdata", *[f"--exclude={model}" for model in EXCLUDED_MODELS], indent=2, stdout=buffer)
        data_path = destination / "data.json.gz"
        with gzip.open(data_path, "wt", encoding="utf-8") as handle:
            handle.write(buffer.getvalue())
        data_path.chmod(0o600)

        files = {"data.json.gz": file_sha256(data_path)}
        media_root = Path(settings.MEDIA_ROOT)
        if not options["without_media"] and media_root.exists():
            media_path = destination / "media.tar.gz"
            with tarfile.open(media_path, "w:gz") as archive:
                for path in sorted(media_root.rglob("*")):
                    if path.is_file():
                        archive.add(path, arcname=path.relative_to(media_root), recursive=False)
            media_path.chmod(0o600)
            files["media.tar.gz"] = file_sha256(media_path)

        manifest = {
            "format": 1,
            "created_at": datetime.now().astimezone().isoformat(),
            "database_engine": settings.DATABASES["default"]["ENGINE"],
            "files": files,
        }
        manifest_path = destination / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_path.chmod(0o600)
        self.stdout.write(self.style.SUCCESS(str(destination)))

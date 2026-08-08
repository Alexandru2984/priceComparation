import hashlib
import json
import shutil
import tarfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_members(archive, destination):
    root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if root not in target.parents and target != root:
            raise CommandError("Arhiva media conține o cale nesigură.")
        if member.issym() or member.islnk():
            raise CommandError("Arhiva media nu poate conține legături simbolice.")
        yield member


class Command(BaseCommand):
    help = "Verifică și restaurează un backup PriceMatch. Operația șterge datele curente."

    def add_arguments(self, parser):
        parser.add_argument("backup", type=Path)
        parser.add_argument("--confirm", help="Trebuie să fie exact RESTORE.")
        parser.add_argument("--without-media", action="store_true")

    def handle(self, *args, **options):
        if options["confirm"] != "RESTORE":
            raise CommandError("Restaurarea este distructivă. Repetă comanda cu --confirm RESTORE.")
        source = options["backup"].expanduser().resolve()
        manifest_path = source / "manifest.json"
        if not manifest_path.is_file():
            raise CommandError("manifest.json lipsește din backup.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != 1:
            raise CommandError("Versiune de backup necunoscută.")
        for filename, expected in manifest.get("files", {}).items():
            path = source / filename
            if not path.is_file() or file_sha256(path) != expected:
                raise CommandError(f"Verificarea SHA-256 a eșuat pentru {filename}.")

        data_path = source / "data.json.gz"
        with transaction.atomic():
            call_command("flush", interactive=False)
            call_command("loaddata", str(data_path))

        media_path = source / "media.tar.gz"
        if not options["without_media"] and media_path.exists():
            destination = Path(settings.MEDIA_ROOT)
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir(parents=True, mode=0o700)
            with tarfile.open(media_path, "r:gz") as archive:
                archive.extractall(destination, members=safe_members(archive, destination), filter="data")
        self.stdout.write(self.style.SUCCESS("Backup restaurat și verificat."))

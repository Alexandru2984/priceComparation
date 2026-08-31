import gzip
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


ALLOWED_BACKUP_FILES = {"data.json.gz", "media.tar.gz"}
MAX_DATABASE_JSON_SIZE = 1024 * 1024 * 1024
MAX_MEDIA_FILES = 50_000
MAX_MEDIA_TOTAL_SIZE = 20 * 1024 * 1024 * 1024
MAX_MEDIA_MEMBER_SIZE = 512 * 1024 * 1024


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_members(archive, destination):
    root = destination.resolve()
    seen = set()
    members = archive.getmembers()
    if len(members) > MAX_MEDIA_FILES:
        raise CommandError("Arhiva media conține prea multe fișiere.")
    if sum(member.size for member in members) > MAX_MEDIA_TOTAL_SIZE:
        raise CommandError("Arhiva media depășește limita totală de restaurare.")
    for member in members:
        if member.name in seen:
            raise CommandError("Arhiva media conține căi duplicate.")
        seen.add(member.name)
        target = (destination / member.name).resolve()
        if root not in target.parents and target != root:
            raise CommandError("Arhiva media conține o cale nesigură.")
        if member.issym() or member.islnk():
            raise CommandError("Arhiva media nu poate conține legături simbolice.")
        if not member.isfile() and not member.isdir():
            raise CommandError("Arhiva media conține un tip de fișier nepermis.")
        if member.size > MAX_MEDIA_MEMBER_SIZE:
            raise CommandError("Arhiva media conține un fișier prea mare.")
        yield member


def validate_database_archive(path):
    total = 0
    try:
        with gzip.open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                total += len(chunk)
                if total > MAX_DATABASE_JSON_SIZE:
                    raise CommandError("Baza comprimată depășește limita de restaurare.")
    except (OSError, EOFError) as exc:
        raise CommandError("Arhiva bazei de date nu este un fișier gzip valid.") from exc


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
        files = manifest.get("files")
        if not isinstance(files, dict) or "data.json.gz" not in files:
            raise CommandError("Manifestul nu declară baza de date obligatorie.")
        if not set(files).issubset(ALLOWED_BACKUP_FILES):
            raise CommandError("Manifestul conține fișiere nepermise.")
        for filename, expected in files.items():
            if not isinstance(expected, str) or len(expected) != 64:
                raise CommandError(f"Semnătură SHA-256 invalidă pentru {filename}.")
            path = source / filename
            if not path.is_file() or file_sha256(path) != expected:
                raise CommandError(f"Verificarea SHA-256 a eșuat pentru {filename}.")

        data_path = source / "data.json.gz"
        validate_database_archive(data_path)
        media_path = source / "media.tar.gz"
        destination = Path(settings.MEDIA_ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="pricematch-media-restore-", dir=destination.parent
        ) as temporary_media:
            staged_media = Path(temporary_media)
            if not options["without_media"] and media_path.exists():
                try:
                    with tarfile.open(media_path, "r:gz") as archive:
                        members = list(safe_members(archive, staged_media))
                        archive.extractall(staged_media, members=members, filter="data")
                except (OSError, tarfile.TarError) as exc:
                    raise CommandError("Arhiva media nu este validă.") from exc

            with transaction.atomic():
                call_command("flush", interactive=False)
                call_command("loaddata", str(data_path))

            if not options["without_media"] and media_path.exists():
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(staged_media, destination)
                destination.chmod(0o700)
        self.stdout.write(self.style.SUCCESS("Backup restaurat și verificat."))

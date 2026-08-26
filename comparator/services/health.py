import shutil
from datetime import datetime
from pathlib import Path

import requests
from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from comparator.models import MetroProductState, MetroScrapeJob


def _check(name, status, detail, action=""):
    return {"name": name, "status": status, "detail": detail, "action": action}


def _database_check():
    try:
        connection.ensure_connection()
        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if pending:
            return _check("Bază de date", "ERROR", f"{len(pending)} migrații neaplicate.", "Rulează migrate")
        engine = "PostgreSQL" if connection.vendor == "postgresql" else connection.vendor
        status = "OK" if connection.vendor == "postgresql" else "WARN"
        return _check("Bază de date", status, f"{engine} conectat · schema este la zi.")
    except Exception as exc:
        return _check("Bază de date", "ERROR", f"Conectarea a eșuat: {str(exc)[:180]}")


def _tesseract_check():
    try:
        import pytesseract

        languages = set(pytesseract.get_languages(config=""))
        requested = set(settings.OCR_LANGUAGE.split("+"))
        missing = requested - languages
        if missing:
            return _check("OCR Tesseract", "ERROR", f"Lipsesc limbile: {', '.join(sorted(missing))}.")
        return _check("OCR Tesseract", "OK", f"Disponibil cu {settings.OCR_LANGUAGE}.")
    except Exception as exc:
        return _check("OCR Tesseract", "ERROR", f"Indisponibil: {str(exc)[:180]}")


def _ollama_check():
    if not settings.OLLAMA_ENABLED:
        return _check("Ollama", "WARN", "Dezactivat; rămâne disponibil parserul determinist.")
    try:
        response = requests.get(f"{settings.OLLAMA_URL.rstrip('/')}/api/tags", timeout=2)
        response.raise_for_status()
        models = {
            row.get("name") or row.get("model")
            for row in response.json().get("models", [])
            if row.get("name") or row.get("model")
        }
        if settings.OLLAMA_MODEL not in models:
            return _check(
                "Ollama",
                "ERROR",
                f"Server activ, dar modelul {settings.OLLAMA_MODEL} nu este instalat.",
                f"ollama pull {settings.OLLAMA_MODEL}",
            )
        return _check("Ollama", "OK", f"Model local disponibil: {settings.OLLAMA_MODEL}.")
    except (requests.RequestException, ValueError) as exc:
        return _check("Ollama", "WARN", f"Nu răspunde momentan: {str(exc)[:180]}")


def _backup_check():
    backup_root = Path(settings.BASE_DIR) / "backups"
    backups = sorted(
        (path for path in backup_root.glob("pricematch-*") if (path / "manifest.json").is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not backups:
        return _check("Backup", "ERROR", "Nu există niciun backup portabil verificabil.")
    latest = backups[0]
    created = datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.get_current_timezone())
    age = timezone.now() - created
    status = "OK" if age.total_seconds() <= 48 * 3600 else "WARN"
    return _check("Backup", status, f"Ultimul: {latest.name} · acum {max(age.days, 0)} zile.")


def _disk_check():
    usage = shutil.disk_usage(settings.BASE_DIR)
    free_gb = usage.free / (1024 ** 3)
    status = "ERROR" if free_gb < 1 else "WARN" if free_gb < 5 else "OK"
    return _check("Spațiu pe disc", status, f"{free_gb:.1f} GB disponibili.")


def _metro_check():
    store = settings.PREFERRED_METRO_STORE.strip() or settings.METRO_STORE_QUERY.strip()
    if not store:
        return _check("Catalog METRO", "ERROR", "Magazinul preferat nu este configurat.")
    states = MetroProductState.objects.filter(store_name__icontains=store)
    latest_job = MetroScrapeJob.objects.filter(status=MetroScrapeJob.Status.COMPLETED).first()
    if not states.exists():
        return _check("Catalog METRO", "WARN", f"{store} configurat, dar fără produse urmărite.")
    last_scan = latest_job.finished_at.strftime("%d.%m.%Y %H:%M") if latest_job and latest_job.finished_at else "necunoscută"
    return _check(
        "Catalog METRO",
        "OK",
        f"{states.filter(available=True).count()} produse disponibile · ultima scanare {last_scan}.",
    )


def system_readiness():
    checks = [
        _database_check(),
        _tesseract_check(),
        _ollama_check(),
        _backup_check(),
        _disk_check(),
        _metro_check(),
        _check(
            "MFA",
            "OK" if settings.MFA_REQUIRED else "WARN",
            "Obligatoriu pentru zona privată." if settings.MFA_REQUIRED else "Opțional în modul local.",
        ),
    ]
    return {
        "checks": checks,
        "ok_count": sum(item["status"] == "OK" for item in checks),
        "warning_count": sum(item["status"] == "WARN" for item in checks),
        "error_count": sum(item["status"] == "ERROR" for item in checks),
    }

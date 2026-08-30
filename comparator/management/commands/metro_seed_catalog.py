import string

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from comparator.catalog import CATEGORY_BREADTH_TERMS, CATEGORY_CHOICES, CATEGORY_SEARCH_TERMS
from comparator.models import MetroScrapeJob, MetroScrapeTerm
from comparator.services.metro_scraper import (
    capture_category_catalog,
    capture_search_terms,
    finalize_catalog_job,
    import_scraped_rows,
)


DEFAULT_TERMS = [term for terms in CATEGORY_SEARCH_TERMS.values() for term in terms]
ALPHABET_TERMS = [first + second for first in string.ascii_lowercase for second in string.ascii_lowercase]


class Command(BaseCommand):
    help = "Caută produse alimentare de bază pe METRO și le importă în catalog."

    def add_arguments(self, parser):
        parser.add_argument("terms", nargs="*", help="Termeni de căutare; implicit se folosesc categoriile de bază.")
        parser.add_argument(
            "--breadth-only",
            action="store_true",
            help="Scanează complet numai căutările largi, pentru extinderea rapidă a catalogului.",
        )
        parser.add_argument(
            "--category-crawl",
            action="store_true",
            help="Descoperă taxonomia METRO și scanează integral fiecare categorie terminală.",
        )
        parser.add_argument(
            "--alphabet-crawl",
            action="store_true",
            help="Scanează toate combinațiile de două litere pentru produse lipsă din taxonomie.",
        )
        parser.add_argument(
            "--limit-per-search", type=int, default=0,
            help="0 înseamnă toate cardurile încărcate; altfel limitează fiecare căutare.",
        )
        parser.add_argument("--delay", type=float, default=0.8)
        parser.add_argument("--retries", type=int, default=3)
        parser.add_argument("--resume", type=int, help="Reia o scanare existentă și sare termenii finalizați.")
        parser.add_argument("--refresh-completed", action="store_true", help="Rulează din nou și termenii finalizați.")
        parser.add_argument("--headed", action="store_true", help="Arată fereastra Chrome.")
        parser.add_argument("--no-import", action="store_true", help="Păstrează rezultatele doar în staging.")
        parser.add_argument(
            "--category",
            choices=[value for value, _ in CATEGORY_CHOICES],
            default="Altele",
            help="Categoria folosită pentru termenii transmiși manual.",
        )
        parser.add_argument(
            "--store",
            default=settings.METRO_STORE_QUERY,
            help="Magazinul METRO care trebuie selectat înainte de scanare.",
        )

    def handle(self, *args, **options):
        selected_modes = sum(bool(value) for value in (
            options["terms"], options["breadth_only"], options["category_crawl"], options["alphabet_crawl"]
        ))
        if selected_modes > 1:
            raise CommandError(
                "Termenii manuali, --breadth-only, --category-crawl și --alphabet-crawl sunt moduri exclusive."
            )
        if options["terms"]:
            terms = options["terms"]
            term_categories = {term: options["category"] for term in terms}
        elif options["alphabet_crawl"]:
            terms = ALPHABET_TERMS
            term_categories = {}
        elif options["breadth_only"]:
            terms = [term for category_terms in CATEGORY_BREADTH_TERMS.values() for term in category_terms]
            term_categories = {
                term: category for category, category_terms in CATEGORY_BREADTH_TERMS.items() for term in category_terms
            }
        else:
            terms = DEFAULT_TERMS
            term_categories = {
                term: category for category, category_terms in CATEGORY_SEARCH_TERMS.items() for term in category_terms
            }
        limit = options["limit_per_search"]
        if options["alphabet_crawl"] and limit == 0:
            limit = 500
        if limit < 0 or limit > 500:
            raise CommandError("--limit-per-search trebuie să fie între 0 și 500.")
        if options["delay"] < 0.3:
            raise CommandError("--delay trebuie să fie cel puțin 0.3 secunde.")
        if options["retries"] < 1 or options["retries"] > 5:
            raise CommandError("--retries trebuie să fie între 1 și 5.")

        if options["resume"]:
            try:
                job = MetroScrapeJob.objects.get(pk=options["resume"])
            except MetroScrapeJob.DoesNotExist as exc:
                raise CommandError("Scanarea cerută nu există.") from exc
            if job.lifecycle_finalized_at and options["refresh_completed"]:
                raise CommandError(
                    "O scanare finalizată nu poate fi refolosită pentru catalog. Pornește una nouă fără --resume."
                )
            if limit and job.scan_type == MetroScrapeJob.ScanType.FULL:
                # O captură plafonată nu dovedește absența produselor care cad
                # după limită și nu trebuie să afecteze disponibilitatea.
                job.scan_type = MetroScrapeJob.ScanType.TARGETED
                job.save(update_fields=["scan_type"])
        else:
            job = MetroScrapeJob.objects.create(
                start_url=settings.METRO_START_URL,
                scan_type=(
                    MetroScrapeJob.ScanType.TARGETED
                    if options["terms"] or options["breadth_only"] or options["alphabet_crawl"] or limit
                    else MetroScrapeJob.ScanType.FULL
                ),
            )
        job.status = MetroScrapeJob.Status.RUNNING
        job.started_at = job.started_at or timezone.now()
        job.finished_at = None
        job.error = ""
        job.save(update_fields=["status", "started_at", "finished_at", "error"])

        def show_progress(index, total, term, count):
            if not options["no_import"]:
                import_scraped_rows(job.products.filter(imported=False))
                job.refresh_from_db(fields=["imported_count"])
            self.stdout.write(
                f"[{index}/{total}] {term}: {count} produse unice, {job.imported_count} importate"
            )

        try:
            capture_options = {
                "delay_seconds": options["delay"],
                "headless": not options["headed"],
                "progress": show_progress,
                "store_query": options["store"],
                "retries": options["retries"],
                "refresh_completed": options["refresh_completed"],
            }
            if options["category_crawl"]:
                captured = capture_category_catalog(
                    job,
                    limit_per_category=limit,
                    **capture_options,
                )
            else:
                captured = capture_search_terms(
                    job,
                    terms,
                    limit_per_search=limit,
                    term_categories=term_categories,
                    **capture_options,
                )
            if not captured:
                raise CommandError(
                    "METRO nu a returnat produse cu preț. Deschide o scanare vizibilă și selectează magazinul."
                )
            if options["no_import"]:
                imported = 0
            else:
                import_scraped_rows(job.products.all())
                job.refresh_from_db(fields=["imported_count"])
                imported = job.imported_count
            failed = job.terms.filter(status=MetroScrapeTerm.Status.ERROR).count()
            job.status = MetroScrapeJob.Status.ERROR if failed else MetroScrapeJob.Status.COMPLETED
            job.error = f"{failed} căutări au eșuat; reia scanarea cu --resume {job.pk}." if failed else ""
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error", "finished_at"])
            if not options["no_import"] and job.status == MetroScrapeJob.Status.COMPLETED:
                job = finalize_catalog_job(job)
            message = f"Gata: {captured} capturate, {imported} importate, {failed} căutări eșuate. Job #{job.pk}."
            self.stdout.write(self.style.WARNING(message) if failed else self.style.SUCCESS(message))
        except Exception as exc:
            job.status = MetroScrapeJob.Status.ERROR
            job.error = str(exc)
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error", "finished_at"])
            raise CommandError(str(exc)) from exc

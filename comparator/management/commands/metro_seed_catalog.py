from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from comparator.models import MetroScrapeJob
from comparator.services.metro_scraper import capture_search_terms, import_scraped_rows


DEFAULT_TERMS = [
    "iaurt", "smantana", "lapte", "branza",
    "suc", "apa plata", "apa minerala",
    "bere", "vin", "vodca",
    "mere", "banane", "portocale", "rosii", "cartofi", "ceapa", "ardei", "castraveti",
    "ulei", "zahar", "faina", "orez", "paste", "oua",
]


class Command(BaseCommand):
    help = "Caută produse alimentare de bază pe METRO și le importă în catalog."

    def add_arguments(self, parser):
        parser.add_argument("terms", nargs="*", help="Termeni de căutare; implicit se folosesc categoriile de bază.")
        parser.add_argument("--limit-per-search", type=int, default=8)
        parser.add_argument("--delay", type=float, default=1.0)
        parser.add_argument("--headed", action="store_true", help="Arată fereastra Chrome.")
        parser.add_argument("--no-import", action="store_true", help="Păstrează rezultatele doar în staging.")
        parser.add_argument(
            "--store",
            default=settings.METRO_STORE_QUERY,
            help="Magazinul METRO care trebuie selectat înainte de scanare.",
        )

    def handle(self, *args, **options):
        terms = options["terms"] or DEFAULT_TERMS
        limit = options["limit_per_search"]
        if limit < 1 or limit > 48:
            raise CommandError("--limit-per-search trebuie să fie între 1 și 48.")
        if options["delay"] < 0:
            raise CommandError("--delay nu poate fi negativ.")

        job = MetroScrapeJob.objects.create(start_url=settings.METRO_START_URL)
        job.status = MetroScrapeJob.Status.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])

        def show_progress(index, total, term, count):
            self.stdout.write(f"[{index}/{total}] {term}: {count} produse unice")

        try:
            captured = capture_search_terms(
                job,
                terms,
                limit_per_search=limit,
                delay_seconds=options["delay"],
                headless=not options["headed"],
                progress=show_progress,
                store_query=options["store"],
            )
            if not captured:
                raise CommandError(
                    "METRO nu a returnat produse cu preț. Deschide o scanare vizibilă și selectează magazinul."
                )
            imported = 0 if options["no_import"] else import_scraped_rows(job.products.all())
            job.status = MetroScrapeJob.Status.COMPLETED
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "finished_at"])
            self.stdout.write(self.style.SUCCESS(f"Gata: {captured} capturate, {imported} importate."))
        except Exception as exc:
            job.status = MetroScrapeJob.Status.ERROR
            job.error = str(exc)
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error", "finished_at"])
            raise CommandError(str(exc)) from exc

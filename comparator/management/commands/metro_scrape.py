from django.core.management.base import BaseCommand, CommandError

from comparator.models import MetroScrapeJob
from comparator.services.metro_scraper import run_scrape_job


class Command(BaseCommand):
    help = "Deschide browserul asistat și capturează produse METRO într-un job de staging."

    def add_arguments(self, parser):
        parser.add_argument("job_id", type=int)

    def handle(self, *args, **options):
        try:
            job = MetroScrapeJob.objects.get(pk=options["job_id"])
        except MetroScrapeJob.DoesNotExist as exc:
            raise CommandError("Jobul METRO nu există.") from exc
        run_scrape_job(job)
        self.stdout.write(self.style.SUCCESS(f"Jobul #{job.pk} s-a încheiat."))


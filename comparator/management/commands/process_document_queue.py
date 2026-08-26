import signal
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from comparator.services.processing_queue import claim_next_job, process_claimed_job, recover_stale_jobs


class Command(BaseCommand):
    help = "Procesează coada locală PostgreSQL pentru OCR și extragerea documentelor."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Procesează cel mult un job și se oprește.")
        parser.add_argument("--poll-seconds", type=float, default=2)
        parser.add_argument("--stale-minutes", type=int, default=30)

    def handle(self, *args, **options):
        if options["poll_seconds"] < 0.2 or options["poll_seconds"] > 60:
            raise CommandError("--poll-seconds trebuie să fie între 0.2 și 60.")
        if options["stale_minutes"] < 1:
            raise CommandError("--stale-minutes trebuie să fie cel puțin 1.")
        should_stop = False

        def stop_worker(signum, frame):
            nonlocal should_stop
            should_stop = True

        signal.signal(signal.SIGTERM, stop_worker)
        signal.signal(signal.SIGINT, stop_worker)
        recovered = recover_stale_jobs(options["stale_minutes"])
        if recovered:
            self.stdout.write(f"Joburi abandonate recuperate: {recovered}")

        while not should_stop:
            # TestCase ține tranzacția de test pe conexiunea curentă. În modul
            # normal, workerul rămâne activ și își reîmprospătează conexiunea.
            if not options["once"]:
                close_old_connections()
            job = claim_next_job()
            if job:
                self.stdout.write(f"Procesez documentul #{job.invoice_id}, job #{job.pk}.")
                succeeded = process_claimed_job(job)
                self.stdout.write(
                    self.style.SUCCESS(f"Job #{job.pk} finalizat.")
                    if succeeded
                    else self.style.ERROR(f"Job #{job.pk} a eșuat.")
                )
            if options["once"]:
                break
            if not job:
                time.sleep(options["poll_seconds"])

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from comparator.models import MetroPriceAnomaly, MetroScrapedProduct, MetroScrapeJob

AUTO_DISMISS_NOTE = (
    "Închisă automat: ambalarea sau unitatea de măsură diferă între capturi; "
    "prețurile normalizate nu sunt comparabile."
)


def _measurement(row):
    return row.units_per_package, row.unit_size, row.base_unit


class Command(BaseCommand):
    help = (
        "Închide, fără ștergeri, anomaliile METRO istorice care compară "
        "ambalări sau unități de măsură diferite."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplică planul; implicit comanda rulează doar în mod simulare.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        if apply_changes and MetroScrapeJob.objects.filter(
            status=MetroScrapeJob.Status.RUNNING
        ).exists():
            raise CommandError(
                "Există o scanare METRO în curs; reconcilierea nu poate fi aplicată simultan."
            )

        counters = {
            "analysed": 0,
            "missing_current": 0,
            "missing_previous": 0,
            "comparable": 0,
        }
        candidates = []

        with transaction.atomic():
            anomaly_query = MetroPriceAnomaly.objects.select_related("state").filter(
                status=MetroPriceAnomaly.Status.OPEN
            )
            if apply_changes:
                anomaly_query = anomaly_query.select_for_update()

            for anomaly in anomaly_query.order_by("pk").iterator(chunk_size=200):
                counters["analysed"] += 1
                capture_query = MetroScrapedProduct.objects.filter(
                    job_id=anomaly.job_id,
                    external_id=anomaly.state.external_id,
                    imported=True,
                )
                if apply_changes:
                    capture_query = capture_query.select_for_update()
                current = capture_query.order_by("-captured_at", "-pk").first()
                if current is None:
                    counters["missing_current"] += 1
                    continue

                previous_query = (
                    MetroScrapedProduct.objects.filter(
                        external_id=current.external_id,
                        store_name=current.store_name,
                        captured_at__lt=current.captured_at,
                        imported=True,
                    )
                    .exclude(pk=current.pk)
                    .order_by("-captured_at", "-pk")
                )
                if apply_changes:
                    previous_query = previous_query.select_for_update()
                previous = previous_query.first()
                if previous is None:
                    counters["missing_previous"] += 1
                    continue

                if _measurement(current) == _measurement(previous):
                    counters["comparable"] += 1
                    continue
                candidates.append(anomaly)

            if apply_changes:
                reviewed_at = timezone.now()
                for anomaly in candidates:
                    updated = MetroPriceAnomaly.objects.filter(
                        pk=anomaly.pk,
                        status=MetroPriceAnomaly.Status.OPEN,
                    ).update(
                        status=MetroPriceAnomaly.Status.DISMISSED,
                        note=AUTO_DISMISS_NOTE,
                        reviewed_at=reviewed_at,
                        reviewed_by=None,
                    )
                    if updated != 1:
                        raise CommandError(
                            "Anomaliile METRO s-au schimbat în timpul reconcilierii; "
                            "tranzacția a fost anulată."
                        )

        mode = "APLICAT" if apply_changes else "SIMULARE"
        self.stdout.write(f"Mod: {mode}")
        self.stdout.write(f"Anomalii deschise analizate: {counters['analysed']}")
        self.stdout.write(f"Capturi curente lipsă: {counters['missing_current']}")
        self.stdout.write(f"Istoric anterior lipsă: {counters['missing_previous']}")
        self.stdout.write(f"Comparații valide păstrate: {counters['comparable']}")
        self.stdout.write(f"Anomalii necomparabile de închis: {len(candidates)}")
        if not apply_changes:
            self.stdout.write("Nu a fost modificată nicio înregistrare. Folosește --apply după backup.")

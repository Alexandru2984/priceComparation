from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from comparator.models import (
    BaseUnit,
    MetroOffer,
    MetroPriceAnomaly,
    MetroProductState,
    MetroScrapedProduct,
    MetroScrapeJob,
)
from comparator.services.metro_scraper import _lowest_price_per_base, explicit_piece_count


def _measurement_differs(instance, units_field, size_field, base_field, pieces):
    return (
        getattr(instance, units_field) != pieces
        or getattr(instance, size_field) != Decimal("1")
        or getattr(instance, base_field) != BaseUnit.PIECE
    )


class Command(BaseCommand):
    help = (
        "Reconciliază, fără ștergeri, măsurătorile METRO pentru produse canonice "
        "la bucată al căror nume declară explicit numărul de bucăți."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Aplică planul; implicit comanda rulează doar în mod simulare.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        if apply_changes and MetroScrapeJob.objects.filter(status=MetroScrapeJob.Status.RUNNING).exists():
            raise CommandError("Există o scanare METRO în curs; corecția nu poate fi aplicată simultan.")

        with transaction.atomic():
            row_query = MetroScrapedProduct.objects.select_related("matched_product").filter(
                matched_product__base_unit=BaseUnit.PIECE
            )
            state_query = MetroProductState.objects.select_related("product").filter(
                product__base_unit=BaseUnit.PIECE
            )
            offer_query = MetroOffer.objects.select_related("product").filter(
                product__base_unit=BaseUnit.PIECE,
                source__startswith="Selenium ",
            )
            anomaly_query = MetroPriceAnomaly.objects.select_related(
                "state", "job", "product"
            ).filter(
                product__base_unit=BaseUnit.PIECE,
                status=MetroPriceAnomaly.Status.OPEN,
            )
            row_changes = []
            for row in row_query.iterator():
                pieces = explicit_piece_count(row.name)
                if pieces and _measurement_differs(
                    row,
                    "units_per_package",
                    "unit_size",
                    "base_unit",
                    pieces,
                ):
                    row_changes.append((row, pieces))

            state_changes = []
            for state in state_query.iterator():
                pieces = explicit_piece_count(state.product.name)
                if pieces and _measurement_differs(
                    state,
                    "last_units_per_package",
                    "last_unit_size",
                    "last_base_unit",
                    pieces,
                ):
                    state_changes.append((state, pieces))

            offer_changes = []
            for offer in offer_query.iterator():
                pieces = explicit_piece_count(offer.product.name)
                if pieces and (
                    offer.units_per_package != pieces or offer.unit_size != Decimal("1")
                ):
                    offer_changes.append((offer, pieces))

            anomaly_changes = []
            for anomaly in anomaly_query.iterator():
                current = MetroScrapedProduct.objects.filter(
                    job=anomaly.job,
                    external_id=anomaly.state.external_id,
                ).first()
                if current is None:
                    continue
                previous = (
                    MetroScrapedProduct.objects.filter(
                        external_id=current.external_id,
                        store_name=current.store_name,
                        captured_at__lt=current.captured_at,
                        imported=True,
                    )
                    .exclude(pk=current.pk)
                    .order_by("-captured_at", "-pk")
                    .first()
                )
                if previous is None:
                    continue
                current_pieces = explicit_piece_count(current.name)
                previous_pieces = explicit_piece_count(previous.name)
                if not current_pieces or not previous_pieces:
                    continue
                measurement_was_wrong = _measurement_differs(
                    current,
                    "units_per_package",
                    "unit_size",
                    "base_unit",
                    current_pieces,
                ) or _measurement_differs(
                    previous,
                    "units_per_package",
                    "unit_size",
                    "base_unit",
                    previous_pieces,
                )
                if not measurement_was_wrong:
                    continue
                old_price = _lowest_price_per_base(
                    previous.price_gross,
                    previous.volume_prices,
                    previous_pieces,
                    Decimal("1"),
                )
                new_price = _lowest_price_per_base(
                    current.price_gross,
                    current.volume_prices,
                    current_pieces,
                    Decimal("1"),
                )
                if not old_price or new_price is None:
                    continue
                corrected_percent = (new_price - old_price) / old_price * Decimal("100")
                if abs(corrected_percent) < Decimal(str(settings.METRO_PRICE_ANOMALY_PERCENT)):
                    anomaly_changes.append((anomaly, corrected_percent))

            if apply_changes:
                now = timezone.now()
                for row, pieces in row_changes:
                    updated = MetroScrapedProduct.objects.filter(
                        pk=row.pk,
                        units_per_package=row.units_per_package,
                        unit_size=row.unit_size,
                        base_unit=row.base_unit,
                    ).update(
                        units_per_package=pieces,
                        unit_size=Decimal("1"),
                        base_unit=BaseUnit.PIECE,
                    )
                    if updated != 1:
                        raise CommandError(
                            "Datele METRO s-au schimbat în timpul corecției; tranzacția a fost anulată."
                        )
                for state, pieces in state_changes:
                    updated = MetroProductState.objects.filter(
                        pk=state.pk,
                        last_units_per_package=state.last_units_per_package,
                        last_unit_size=state.last_unit_size,
                        last_base_unit=state.last_base_unit,
                    ).update(
                        last_units_per_package=pieces,
                        last_unit_size=Decimal("1"),
                        last_base_unit=BaseUnit.PIECE,
                    )
                    if updated != 1:
                        raise CommandError(
                            "Starea METRO s-a schimbat în timpul corecției; tranzacția a fost anulată."
                        )
                for offer, pieces in offer_changes:
                    updated = MetroOffer.objects.filter(
                        pk=offer.pk,
                        units_per_package=offer.units_per_package,
                        unit_size=offer.unit_size,
                    ).update(
                        units_per_package=pieces,
                        unit_size=Decimal("1"),
                        updated_at=now,
                    )
                    if updated != 1:
                        raise CommandError(
                            "Ofertele METRO s-au schimbat în timpul corecției; tranzacția a fost anulată."
                        )
                for anomaly, corrected_percent in anomaly_changes:
                    note = (
                        "Închisă automat după reconcilierea ambalajului; "
                        f"variația comparabilă este {corrected_percent:.2f}%."
                    )[:300]
                    updated = MetroPriceAnomaly.objects.filter(
                        pk=anomaly.pk,
                        status=MetroPriceAnomaly.Status.OPEN,
                    ).update(
                        status=MetroPriceAnomaly.Status.DISMISSED,
                        note=note,
                        reviewed_at=now,
                        reviewed_by=None,
                    )
                    if updated != 1:
                        raise CommandError(
                            "Anomaliile METRO s-au schimbat în timpul corecției; tranzacția a fost anulată."
                        )

            mode = "APLICAT" if apply_changes else "SIMULARE"
            self.stdout.write(f"Mod: {mode}")
            self.stdout.write(f"Rânduri de captură: {len(row_changes)}")
            self.stdout.write(f"Stări curente: {len(state_changes)}")
            self.stdout.write(f"Oferte Selenium: {len(offer_changes)}")
            self.stdout.write(f"Anomalii false de închis: {len(anomaly_changes)}")
            if not apply_changes:
                self.stdout.write("Nu a fost modificată nicio înregistrare. Folosește --apply după backup.")

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from comparator.models import (
    MetroOffer,
    MetroPriceAnomaly,
    MetroProductState,
    MetroScrapedProduct,
    MetroScrapeJob,
    Product,
)


class MetroMeasurementCorrectionCommandTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            name="Pampers Servetele 4 x 80 bucati",
            base_unit="BUC",
        )
        self.previous_job = MetroScrapeJob.objects.create(
            status=MetroScrapeJob.Status.COMPLETED,
            start_url="https://produse.metro.ro/shop",
            store_name="METRO TEST",
        )
        self.current_job = MetroScrapeJob.objects.create(
            status=MetroScrapeJob.Status.COMPLETED,
            start_url="https://produse.metro.ro/shop",
            store_name="METRO TEST",
        )
        self.previous = MetroScrapedProduct.objects.create(
            job=self.previous_job,
            external_id="METRO-PAMPERS",
            name=self.product.name,
            product_url="https://produse.metro.ro/shop/pv/METRO-PAMPERS/old",
            store_name="METRO TEST",
            units_per_package=80,
            unit_size=1,
            base_unit="BUC",
            price_gross=Decimal("43.95"),
            matched_product=self.product,
            imported=True,
        )
        self.current = MetroScrapedProduct.objects.create(
            job=self.current_job,
            external_id="METRO-PAMPERS",
            name=self.product.name,
            product_url="https://produse.metro.ro/shop/pv/METRO-PAMPERS/new",
            store_name="METRO TEST",
            units_per_package=4,
            unit_size=1,
            base_unit="BUC",
            price_gross=Decimal("43.95"),
            matched_product=self.product,
            imported=True,
        )
        now = timezone.now()
        MetroScrapedProduct.objects.filter(pk=self.previous.pk).update(captured_at=now - timedelta(days=1))
        MetroScrapedProduct.objects.filter(pk=self.current.pk).update(captured_at=now)
        self.previous.refresh_from_db()
        self.current.refresh_from_db()
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=80,
            unit_size=1,
            price_gross=Decimal("43.95"),
            valid_from=date(2026, 8, 31),
            source="Selenium METRO TEST",
        )
        MetroOffer.objects.create(
            product=self.product,
            units_per_package=4,
            unit_size=1,
            price_gross=Decimal("43.95"),
            valid_from=date(2026, 9, 1),
            source="Selenium METRO TEST",
        )
        self.state = MetroProductState.objects.create(
            product=self.product,
            external_id="METRO-PAMPERS",
            store_name="METRO TEST",
            first_seen_at=self.previous.captured_at,
            last_seen_at=self.current.captured_at,
            first_seen_job=self.previous_job,
            last_seen_job=self.current_job,
            last_price_gross=Decimal("43.95"),
            last_units_per_package=4,
            last_unit_size=1,
            last_base_unit="BUC",
        )
        self.anomaly = MetroPriceAnomaly.objects.create(
            state=self.state,
            job=self.current_job,
            product=self.product,
            old_price_per_base=Decimal("0.5494"),
            new_price_per_base=Decimal("10.9875"),
            change_percent=Decimal("1900"),
        )

    def test_dry_run_reports_without_changing_data(self):
        output = StringIO()

        call_command("correct_metro_measurements", stdout=output)

        self.current.refresh_from_db()
        self.anomaly.refresh_from_db()
        self.assertIn("Mod: SIMULARE", output.getvalue())
        self.assertEqual(self.current.units_per_package, Decimal("4"))
        self.assertEqual(self.anomaly.status, MetroPriceAnomaly.Status.OPEN)

    def test_apply_reconciles_rows_state_offers_and_false_anomaly(self):
        output = StringIO()

        call_command("correct_metro_measurements", "--apply", stdout=output)

        self.current.refresh_from_db()
        self.state.refresh_from_db()
        self.anomaly.refresh_from_db()
        self.assertIn("Mod: APLICAT", output.getvalue())
        self.assertEqual(self.current.units_per_package, Decimal("320"))
        self.assertEqual(self.state.last_units_per_package, Decimal("320"))
        self.assertFalse(
            self.product.metro_offers.exclude(
                units_per_package=Decimal("320"),
                unit_size=Decimal("1"),
            ).exists()
        )
        self.assertEqual(self.anomaly.status, MetroPriceAnomaly.Status.DISMISSED)
        self.assertIn("0.00%", self.anomaly.note)

    def test_kilogram_product_is_never_rewritten(self):
        product = Product.objects.create(name="Mici congelați 16 bucati", base_unit="KG")
        offer = MetroOffer.objects.create(
            product=product,
            units_per_package=1,
            unit_size=Decimal("0.360"),
            price_gross=20,
            valid_from=date(2026, 9, 1),
            source="Selenium METRO TEST",
        )

        call_command("correct_metro_measurements", "--apply", stdout=StringIO())

        offer.refresh_from_db()
        self.assertEqual(offer.units_per_package, Decimal("1"))
        self.assertEqual(offer.unit_size, Decimal("0.360"))

from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from comparator.models import (
    MetroPriceAnomaly,
    MetroProductState,
    MetroScrapedProduct,
    MetroScrapeJob,
    Product,
)


class MetroAnomalyReconciliationCommandTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Produs 4 x 80 bucăți", base_unit="BUC")
        self.previous_job = self._job()
        self.current_job = self._job()
        self.previous = self._capture(
            self.previous_job,
            units=Decimal("320"),
            price=Decimal("43.95"),
        )
        self.current = self._capture(
            self.current_job,
            units=Decimal("4"),
            price=Decimal("43.95"),
        )
        now = timezone.now()
        MetroScrapedProduct.objects.filter(pk=self.previous.pk).update(
            captured_at=now - timedelta(days=1)
        )
        MetroScrapedProduct.objects.filter(pk=self.current.pk).update(captured_at=now)
        self.previous.refresh_from_db()
        self.current.refresh_from_db()
        self.state = MetroProductState.objects.create(
            product=self.product,
            external_id="METRO-TEST",
            store_name="METRO TEST",
            first_seen_at=self.previous.captured_at,
            last_seen_at=self.current.captured_at,
            first_seen_job=self.previous_job,
            last_seen_job=self.current_job,
            last_price_gross=self.current.price_gross,
            last_units_per_package=self.current.units_per_package,
            last_unit_size=self.current.unit_size,
            last_base_unit=self.current.base_unit,
        )
        self.anomaly = MetroPriceAnomaly.objects.create(
            state=self.state,
            job=self.current_job,
            product=self.product,
            old_price_per_base=Decimal("0.1373"),
            new_price_per_base=Decimal("10.9875"),
            change_percent=Decimal("7900"),
        )

    @staticmethod
    def _job(status=MetroScrapeJob.Status.COMPLETED):
        return MetroScrapeJob.objects.create(
            status=status,
            start_url="https://produse.metro.ro/shop",
            store_name="METRO TEST",
        )

    def _capture(self, job, *, units, price):
        return MetroScrapedProduct.objects.create(
            job=job,
            external_id="METRO-TEST",
            name=self.product.name,
            product_url=f"https://produse.metro.ro/shop/pv/{job.pk}",
            store_name="METRO TEST",
            units_per_package=units,
            unit_size=Decimal("1"),
            base_unit="BUC",
            price_gross=price,
            matched_product=self.product,
            imported=True,
        )

    def test_dry_run_reports_candidate_without_changing_it(self):
        output = StringIO()

        call_command("reconcile_metro_anomalies", stdout=output)

        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.status, MetroPriceAnomaly.Status.OPEN)
        self.assertIn("Mod: SIMULARE", output.getvalue())
        self.assertIn("Anomalii necomparabile de închis: 1", output.getvalue())

    def test_apply_dismisses_only_noncomparable_anomaly(self):
        output = StringIO()

        call_command("reconcile_metro_anomalies", "--apply", stdout=output)

        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.status, MetroPriceAnomaly.Status.DISMISSED)
        self.assertIsNotNone(self.anomaly.reviewed_at)
        self.assertIsNone(self.anomaly.reviewed_by)
        self.assertIn("nu sunt comparabile", self.anomaly.note)
        self.assertIn("Mod: APLICAT", output.getvalue())

    def test_same_measurement_remains_open_for_human_review(self):
        MetroScrapedProduct.objects.filter(pk=self.previous.pk).update(
            units_per_package=self.current.units_per_package,
            unit_size=self.current.unit_size,
            base_unit=self.current.base_unit,
        )

        call_command("reconcile_metro_anomalies", "--apply", stdout=StringIO())

        self.anomaly.refresh_from_db()
        self.assertEqual(self.anomaly.status, MetroPriceAnomaly.Status.OPEN)

    def test_apply_refuses_to_run_during_active_scan(self):
        self._job(status=MetroScrapeJob.Status.RUNNING)

        with self.assertRaisesMessage(CommandError, "scanare METRO în curs"):
            call_command("reconcile_metro_anomalies", "--apply", stdout=StringIO())

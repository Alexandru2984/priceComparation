from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from comparator.models import (
    AutomationRun,
    InventoryItem,
    MetroOffer,
    MetroPriceAnomaly,
    MetroProductState,
    MetroScrapedProduct,
    MetroScrapeJob,
    MetroScrapeTerm,
    Product,
)
from comparator.services.metro_scraper import finalize_catalog_job, import_scraped_rows

STORE = "METRO PUNCT TARGOVISTE"


class MetroLifecycleTests(TestCase):
    def complete_job_with_product(self, external_id, name, price="5.00"):
        job = MetroScrapeJob.objects.create(
            status=MetroScrapeJob.Status.COMPLETED,
            scan_type=MetroScrapeJob.ScanType.FULL,
            start_url="https://produse.metro.ro/shop",
            store_name=STORE,
            total_queries=1,
            completed_queries=1,
            finished_at=timezone.now(),
        )
        MetroScrapeTerm.objects.create(
            job=job,
            term="catalog test",
            status=MetroScrapeTerm.Status.COMPLETED,
            found_count=1,
        )
        row = MetroScrapedProduct.objects.create(
            job=job,
            external_id=external_id,
            name=name,
            product_url=f"https://produse.metro.ro/shop/pv/{external_id}/test",
            store_name=STORE,
            package_text="1 BUCATA",
            units_per_package=1,
            unit_size=1,
            base_unit="BUC",
            price_gross=Decimal(price),
        )
        import_scraped_rows(job.products.all())
        row.refresh_from_db()
        job.captured_count = 1
        job.save(update_fields=["captured_count"])
        return finalize_catalog_job(job), row

    def test_import_tracks_first_and_last_seen_for_store(self):
        first_job, first_row = self.complete_job_with_product("METRO-1", "Produs urmărit", "5.00")
        state = MetroProductState.objects.get(external_id="METRO-1", store_name=STORE)
        self.assertEqual(state.first_seen_job, first_job)
        self.assertEqual(state.last_seen_job, first_job)
        self.assertTrue(state.available)
        self.assertEqual(first_job.new_products_count, 1)

        second_job, _ = self.complete_job_with_product("METRO-1", "Produs urmărit", "4.50")
        state.refresh_from_db()
        self.assertEqual(state.first_seen_job, first_job)
        self.assertEqual(state.last_seen_job, second_job)
        self.assertEqual(state.last_price_gross, Decimal("4.50"))
        self.assertEqual(state.consecutive_misses, 0)
        self.assertEqual(MetroProductState.objects.count(), 1)
        second_job.refresh_from_db()
        self.assertEqual(second_job.price_changes_count, 1)
        first_row.refresh_from_db()
        self.assertTrue(first_row.imported)

    def test_two_complete_misses_deactivate_offer_and_reappearance_reactivates_it(self):
        first_job, target_row = self.complete_job_with_product("TARGET", "Produs temporar", "9.00")
        target_product = target_row.matched_product
        self.assertTrue(MetroOffer.objects.filter(product=target_product, active=True).exists())

        miss_one, _ = self.complete_job_with_product("OTHER", "Alt produs", "2.00")
        state = MetroProductState.objects.get(external_id="TARGET", store_name=STORE)
        state.refresh_from_db()
        miss_one.refresh_from_db()
        self.assertEqual(state.consecutive_misses, 1)
        self.assertTrue(state.available)
        self.assertEqual(miss_one.missing_products_count, 1)
        self.assertEqual(miss_one.unavailable_products_count, 0)

        miss_two, _ = self.complete_job_with_product("OTHER", "Alt produs", "2.10")
        state.refresh_from_db()
        miss_two.refresh_from_db()
        self.assertEqual(state.consecutive_misses, 2)
        self.assertFalse(state.available)
        self.assertEqual(miss_two.unavailable_products_count, 1)
        self.assertFalse(MetroOffer.objects.filter(product=target_product, active=True).exists())

        reappeared, _ = self.complete_job_with_product("TARGET", "Produs temporar", "8.50")
        state.refresh_from_db()
        reappeared.refresh_from_db()
        self.assertTrue(state.available)
        self.assertEqual(state.consecutive_misses, 0)
        self.assertEqual(state.reactivated_in_job, reappeared)
        self.assertEqual(reappeared.reactivated_products_count, 1)
        self.assertTrue(MetroOffer.objects.filter(product=target_product, active=True).exists())

    def test_partial_scan_never_marks_other_products_missing(self):
        _, target_row = self.complete_job_with_product("SAFE", "Produs protejat", "3.00")
        partial = MetroScrapeJob.objects.create(
            status=MetroScrapeJob.Status.ERROR,
            scan_type=MetroScrapeJob.ScanType.FULL,
            start_url="https://produse.metro.ro/shop",
            store_name=STORE,
            total_queries=2,
            completed_queries=1,
        )
        MetroScrapeTerm.objects.create(
            job=partial,
            term="eroare",
            status=MetroScrapeTerm.Status.ERROR,
        )
        finalize_catalog_job(partial)
        state = MetroProductState.objects.get(product=target_row.matched_product, store_name=STORE)
        partial.refresh_from_db()
        self.assertEqual(state.consecutive_misses, 0)
        self.assertIsNone(partial.lifecycle_finalized_at)

    def test_targeted_scan_never_marks_unsearched_products_missing(self):
        _, target_row = self.complete_job_with_product("SAFE-TARGETED", "Produs protejat țintit", "3.00")
        targeted = MetroScrapeJob.objects.create(
            status=MetroScrapeJob.Status.COMPLETED,
            scan_type=MetroScrapeJob.ScanType.TARGETED,
            start_url="https://produse.metro.ro/shop",
            store_name=STORE,
            total_queries=1,
            completed_queries=1,
            finished_at=timezone.now(),
        )
        MetroScrapeTerm.objects.create(
            job=targeted,
            term="alt produs",
            status=MetroScrapeTerm.Status.COMPLETED,
        )
        other = MetroScrapedProduct.objects.create(
            job=targeted,
            external_id="TARGETED-OTHER",
            name="Alt produs țintit",
            product_url="https://produse.metro.ro/shop/pv/TARGETED-OTHER/test",
            store_name=STORE,
            price_gross=2,
        )
        import_scraped_rows(targeted.products.all())
        finalize_catalog_job(targeted)

        state = MetroProductState.objects.get(product=target_row.matched_product, store_name=STORE)
        self.assertEqual(state.consecutive_misses, 0)
        self.assertTrue(state.available)
        self.assertIsNotNone(other.pk)

    def test_large_normalized_price_change_creates_review_anomaly(self):
        self.complete_job_with_product("ANOMALY", "Produs cu abatere", "5.00")
        second_job, row = self.complete_job_with_product("ANOMALY", "Produs cu abatere", "8.00")

        anomaly = MetroPriceAnomaly.objects.get(job=second_job, product=row.matched_product)

        self.assertEqual(anomaly.old_price_per_base, Decimal("5"))
        self.assertEqual(anomaly.new_price_per_base, Decimal("8"))
        self.assertEqual(anomaly.change_percent, Decimal("60"))
        self.assertEqual(anomaly.status, MetroPriceAnomaly.Status.OPEN)


class MetroAutomationTests(TestCase):
    @override_settings(
        METRO_API_ENABLED=True,
        METRO_SELENIUM_ENABLED=False,
        METRO_STORE_QUERY="Targoviste",
    )
    @patch("comparator.management.commands.pricematch_maintenance.launch_api_catalog_job")
    def test_full_maintenance_uses_fast_api_catalog(self, launcher):
        call_command(
            "pricematch_maintenance",
            skip_backup=True,
            skip_notifications=True,
            scan_metro=True,
            verbosity=0,
        )

        job = MetroScrapeJob.objects.get(scan_type=MetroScrapeJob.ScanType.FULL)
        launcher.assert_called_once_with(job, "Targoviste")

    @override_settings(
        METRO_API_ENABLED=True,
        METRO_SELENIUM_ENABLED=True,
        METRO_FULL_SCAN_INTERVAL_DAYS=7,
        METRO_TARGETED_SCAN_INTERVAL_HOURS=24,
        METRO_TARGETED_SCAN_MAX_PRODUCTS=150,
        METRO_STORE_QUERY="Targoviste",
    )
    @patch("comparator.management.commands.pricematch_maintenance.launch_targeted_catalog_job")
    def test_scheduled_maintenance_launches_tracked_products_after_recent_full_scan(self, launcher):
        MetroScrapeJob.objects.create(
            status=MetroScrapeJob.Status.COMPLETED,
            scan_type=MetroScrapeJob.ScanType.FULL,
            start_url="https://produse.metro.ro/shop",
            finished_at=timezone.now(),
        )
        product = Product.objects.create(name="Produs urmărit zilnic", base_unit="BUC")
        InventoryItem.objects.create(product=product)

        call_command(
            "pricematch_maintenance",
            skip_backup=True,
            skip_notifications=True,
            scheduled_metro=True,
            verbosity=0,
        )

        job = MetroScrapeJob.objects.get(scan_type=MetroScrapeJob.ScanType.TARGETED)
        launcher.assert_called_once_with(job, [product.name], "Targoviste")
        run = AutomationRun.objects.get()
        self.assertEqual(run.status, AutomationRun.Status.COMPLETED)
        self.assertEqual(run.metro_job, job)

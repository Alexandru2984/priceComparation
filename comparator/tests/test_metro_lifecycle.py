from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from comparator.models import (
    MetroOffer,
    MetroProductState,
    MetroScrapeJob,
    MetroScrapedProduct,
    MetroScrapeTerm,
)
from comparator.services.metro_scraper import finalize_catalog_job, import_scraped_rows


STORE = "METRO PUNCT TARGOVISTE"


class MetroLifecycleTests(TestCase):
    def complete_job_with_product(self, external_id, name, price="5.00"):
        job = MetroScrapeJob.objects.create(
            status=MetroScrapeJob.Status.COMPLETED,
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

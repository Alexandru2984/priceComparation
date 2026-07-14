from decimal import Decimal

from django.test import TestCase

from comparator.models import MetroScrapeJob, MetroScrapedProduct, Product
from comparator.services.metro_scraper import (
    import_scraped_rows,
    normalize_dom_rows,
    parse_measurement,
    store_captured_rows,
)


class MetroNormalizationTests(TestCase):
    def test_normalizes_visible_product_card_without_images(self):
        rows = normalize_dom_rows(
            [
                {
                    "name": "aro Apa Carbogazoasa SGR 6 x 2 L",
                    "product_url": "https://produse.metro.ro/shop/pv/BTY-X793490/0032/0021/produs",
                    "package_text": "6IMP. IN FOLIE",
                    "store_text": "disponibil in METRO PALLADY",
                    "price_text": "incl. TVA 10,16 RON",
                }
            ]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_id"], "BTY-X793490")
        self.assertEqual(rows[0]["price_gross"], Decimal("10.16"))
        self.assertEqual(rows[0]["units_per_package"], Decimal("6"))
        self.assertEqual(rows[0]["unit_size"], Decimal("2"))
        self.assertEqual(rows[0]["base_unit"], "L")
        self.assertEqual(rows[0]["store_name"], "METRO PALLADY")

    def test_variable_weight_package_uses_one_kilogram_price(self):
        units, size, base_unit = parse_measurement("Piept pui gastro 4,5 Kg", "1 KILOGRAM")
        self.assertEqual((units, size, base_unit), (Decimal("1"), Decimal("1"), "KG"))

    def test_piece_count_is_read_from_product_name(self):
        units, size, base_unit = parse_measurement("aro Oua M 20 buc", "1 CASEROLA")
        self.assertEqual((units, size, base_unit), (Decimal("20"), Decimal("1"), "BUC"))


class MetroStagingTests(TestCase):
    def test_stages_and_imports_selected_product(self):
        job = MetroScrapeJob.objects.create(start_url="https://produse.metro.ro/shop")
        store_captured_rows(
            job,
            [
                {
                    "external_id": "BTY-X1",
                    "name": "Ulei test 1 L",
                    "product_url": "https://produse.metro.ro/shop/pv/BTY-X1/0032/0021/test",
                    "store_name": "METRO PALLADY",
                    "package_text": "1 BUCATA",
                    "units_per_package": Decimal("1"),
                    "unit_size": Decimal("1"),
                    "base_unit": "L",
                    "price_gross": Decimal("8.50"),
                }
            ],
        )
        row = MetroScrapedProduct.objects.get(job=job)
        self.assertFalse(row.imported)
        self.assertEqual(import_scraped_rows(job.products.all()), 1)
        row.refresh_from_db()
        product = Product.objects.get(name="Ulei test 1 L")
        self.assertTrue(row.imported)
        self.assertEqual(product.metro_offers.get().price_gross, Decimal("8.50"))

    def test_does_not_merge_a_weak_fuzzy_match(self):
        existing = Product.objects.create(name="PFANNER Suc Ananas 1 L", brand="", base_unit="L")
        job = MetroScrapeJob.objects.create(start_url="https://produse.metro.ro/shop")
        store_captured_rows(
            job,
            [
                {
                    "external_id": "BTY-X2",
                    "name": "aro Lapte Consum 1.5% grasime 1 L",
                    "product_url": "https://produse.metro.ro/shop/pv/BTY-X2/0032/0021/test",
                    "store_name": "METRO PALLADY",
                    "package_text": "1 STICLA",
                    "units_per_package": Decimal("1"),
                    "unit_size": Decimal("1"),
                    "base_unit": "L",
                    "price_gross": Decimal("3.50"),
                }
            ],
        )
        row = MetroScrapedProduct.objects.get(job=job)
        self.assertGreaterEqual(row.match_score, 80)
        self.assertNotEqual(row.matched_product, existing)
        self.assertIsNone(row.matched_product)

    def test_scan_pages_load(self):
        job = MetroScrapeJob.objects.create(start_url="https://produse.metro.ro/shop")
        self.assertEqual(self.client.get("/metro/scanari/").status_code, 200)
        self.assertEqual(self.client.get(f"/metro/scanari/{job.pk}/").status_code, 200)

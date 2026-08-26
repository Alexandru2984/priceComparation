from decimal import Decimal
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from comparator.models import MetroScrapeJob, MetroScrapedProduct, Product, ProductCode
from comparator.services.metro_scraper import (
    _load_all_visible_cards,
    import_scraped_rows,
    normalize_dom_rows,
    parse_measurement,
    store_captured_rows,
)


class MetroNormalizationTests(TestCase):
    def test_load_more_collects_all_search_pages(self):
        driver = MagicMock()
        state = {"cards": 24}
        control = MagicMock()
        control.is_displayed.return_value = True

        def find_elements(by, selector):
            if selector == ".sd-articlecard":
                return [object()] * state["cards"]
            if selector == ".mfcss_load-more-articles":
                return [control] if state["cards"] < 72 else []
            return []

        def execute_script(script, *args):
            if "click" in script:
                state["cards"] += 24

        driver.find_elements.side_effect = find_elements
        driver.execute_script.side_effect = execute_script

        self.assertEqual(_load_all_visible_cards(driver), 72)
        self.assertEqual(driver.execute_script.call_count, 4)

    def test_load_more_respects_targeted_result_limit(self):
        driver = MagicMock()
        driver.find_elements.side_effect = lambda by, selector: (
            [object()] * 24 if selector == ".sd-articlecard" else []
        )

        self.assertEqual(_load_all_visible_cards(driver, max_cards=8), 24)
        driver.execute_script.assert_not_called()

    def test_normalizes_visible_product_card_without_images(self):
        rows = normalize_dom_rows(
            [
                {
                    "name": "aro Apa Carbogazoasa SGR 6 x 2 L",
                    "product_url": "https://produse.metro.ro/shop/pv/BTY-X793490/0032/0021/produs",
                    "package_text": "6IMP. IN FOLIE",
                    "store_text": "disponibil in METRO PALLADY",
                    "price_text": "incl. TVA 10,16 RON",
                    "volume_price_texts": ["9,45 RON pentru 3+"],
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
        self.assertEqual(
            rows[0]["volume_prices"],
            [{"min_packages": 3, "price_gross": "9.45", "label": "9,45 RON pentru 3+"}],
        )

    def test_variable_weight_package_uses_one_kilogram_price(self):
        units, size, base_unit = parse_measurement("Piept pui gastro 4,5 Kg", "1 KILOGRAM")
        self.assertEqual((units, size, base_unit), (Decimal("1"), Decimal("1"), "KG"))

    def test_piece_count_is_read_from_product_name(self):
        units, size, base_unit = parse_measurement("aro Oua M 20 buc", "1 CASEROLA")
        self.assertEqual((units, size, base_unit), (Decimal("20"), Decimal("1"), "BUC"))


class MetroStagingTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="metro-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)

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
                    "volume_prices": [
                        {"min_packages": 6, "price_gross": "7.90", "label": "7,90 RON pentru 6+"}
                    ],
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
        tier = product.metro_offers.get().volume_tiers.get()
        self.assertEqual(tier.min_packages, 6)
        self.assertEqual(tier.price_gross, Decimal("7.90"))

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
        self.assertEqual(self.client.get("/app/metro/scanari/").status_code, 200)
        self.assertEqual(self.client.get(f"/app/metro/scanari/{job.pk}/").status_code, 200)

    def test_metro_identity_is_saved_when_a_row_is_imported(self):
        job = MetroScrapeJob.objects.create(start_url="https://produse.metro.ro/shop")
        store_captured_rows(
            job,
            [{
                "external_id": "BTY-PERSIST",
                "name": "Produs persistent 1 buc",
                "product_url": "https://produse.metro.ro/shop/pv/BTY-PERSIST/x",
                "store_name": "METRO PUNCT TARGOVISTE",
                "package_text": "1 BUCATA",
                "units_per_package": Decimal("1"),
                "unit_size": Decimal("1"),
                "base_unit": "BUC",
                "price_gross": Decimal("3.50"),
            }],
        )
        import_scraped_rows(job.products.all())
        self.assertTrue(ProductCode.objects.filter(kind="METRO", code="BTY-PERSIST").exists())

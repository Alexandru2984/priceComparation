from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from comparator.models import (
    MetroOffer,
    MetroOfferTier,
    MetroScrapedProduct,
    MetroScrapeJob,
    Product,
    ProductCode,
)
from comparator.services.metro_scraper import (
    _direct_category_children,
    _load_all_visible_cards,
    _rows_matching_exact_term,
    import_scraped_rows,
    normalize_dom_rows,
    parse_measurement,
    store_captured_rows,
)


class MetroNormalizationTests(TestCase):
    def test_alphabet_rows_ignore_fuzzy_search_fallbacks(self):
        rows = [
            {"name": "Căpșuni 500 g"},
            {"name": "Apa plată 2 L"},
            {"name": "Produs fără legătură"},
        ]

        self.assertEqual(
            _rows_matching_exact_term(rows, "ap"),
            rows[:2],
        )
    def test_category_discovery_keeps_only_direct_children(self):
        current = "/shop/category/alimentare/bacanie"
        candidates = {
            "/shop/category/alimentare",
            current,
            f"{current}/paste",
            f"{current}/condimente",
            f"{current}/condimente/piper",
            "/shop/category/nealimentare/cosmetice",
        }

        self.assertEqual(
            _direct_category_children(current, candidates),
            [f"{current}/condimente", f"{current}/paste"],
        )

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
        self.staff = get_user_model().objects.create_superuser(
            username="metro-admin", password="A-test-password-2026!"
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

    @override_settings(PREFERRED_METRO_STORE="METRO PUNCT TARGOVISTE")
    def test_metro_list_can_show_only_volume_price_offers(self):
        volume_product = Product.objects.create(name="Produs cu prag", base_unit="BUC")
        regular_product = Product.objects.create(name="Produs fără prag", base_unit="BUC")
        volume_offer = MetroOffer.objects.create(
            product=volume_product,
            price_gross=10,
            valid_from=date(2026, 8, 27),
            source="Selenium METRO PUNCT TARGOVISTE",
        )
        MetroOfferTier.objects.create(offer=volume_offer, min_packages=3, price_gross=8)
        MetroOffer.objects.create(
            product=regular_product,
            price_gross=10,
            valid_from=date(2026, 8, 27),
            source="Selenium METRO PUNCT TARGOVISTE",
        )

        response = self.client.get("/app/metro/?volume=with")

        self.assertContains(response, "Produs cu prag")
        self.assertNotContains(response, "Produs fără prag")
        self.assertContains(response, "3+ pachete")
        self.assertContains(response, "6,00 lei economisiți")

    @override_settings(PREFERRED_METRO_STORE="METRO PUNCT TARGOVISTE")
    def test_metro_list_can_rank_volume_savings(self):
        small = Product.objects.create(name="Economie mică", base_unit="BUC")
        large = Product.objects.create(name="Economie mare", base_unit="BUC")
        small_offer = MetroOffer.objects.create(
            product=small,
            price_gross=10,
            valid_from=date(2026, 8, 27),
            source="Selenium METRO PUNCT TARGOVISTE",
        )
        large_offer = MetroOffer.objects.create(
            product=large,
            price_gross=10,
            valid_from=date(2026, 8, 27),
            source="Selenium METRO PUNCT TARGOVISTE",
        )
        MetroOfferTier.objects.create(offer=small_offer, min_packages=3, price_gross=9)
        MetroOfferTier.objects.create(offer=large_offer, min_packages=3, price_gross=6)

        response = self.client.get("/app/metro/?volume=with&sort=saving")
        rendered_offers = list(response.context["offers"])

        self.assertEqual([offer.product for offer in rendered_offers], [large, small])
        self.assertContains(response, "Cea mai mare economie")

    @override_settings(PREFERRED_METRO_STORE="METRO PUNCT TARGOVISTE")
    def test_metro_list_shows_only_latest_preferred_store_offer_by_default(self):
        product = Product.objects.create(name="Produs cu istoric", base_unit="BUC")
        old_offer = MetroOffer.objects.create(
            product=product,
            price_gross=12,
            valid_from=date(2026, 8, 26),
            source="Selenium METRO PUNCT TARGOVISTE",
        )
        current_offer = MetroOffer.objects.create(
            product=product,
            price_gross=10,
            valid_from=date(2026, 8, 27),
            source="Selenium METRO PUNCT TARGOVISTE",
        )
        MetroOffer.objects.create(
            product=product,
            price_gross=9,
            valid_from=date(2026, 8, 27),
            source="Selenium METRO PALLADY",
        )

        response = self.client.get("/app/metro/")
        rendered_offers = list(response.context["offers"])

        self.assertEqual(rendered_offers, [current_offer])
        self.assertNotIn(old_offer, rendered_offers)

    @override_settings(PREFERRED_METRO_STORE="METRO PUNCT TARGOVISTE")
    def test_metro_list_can_show_full_history_and_all_stores(self):
        product = Product.objects.create(name="Produs din două magazine", base_unit="BUC")
        for source, day in [
            ("Selenium METRO PUNCT TARGOVISTE", 26),
            ("Selenium METRO PUNCT TARGOVISTE", 27),
            ("Selenium METRO PALLADY", 27),
        ]:
            MetroOffer.objects.create(
                product=product,
                price_gross=10,
                valid_from=date(2026, 8, day),
                source=source,
            )

        response = self.client.get("/app/metro/?snapshot=history&location=all")

        self.assertEqual(len(list(response.context["offers"])), 3)
        self.assertContains(response, "Tot istoricul")

    @override_settings(PREFERRED_METRO_STORE="METRO PUNCT TARGOVISTE")
    def test_volume_stat_counts_only_current_offers(self):
        product = Product.objects.create(name="Prag expirat", base_unit="BUC")
        old_offer = MetroOffer.objects.create(
            product=product,
            price_gross=10,
            valid_from=date(2026, 8, 26),
            source="Selenium METRO PUNCT TARGOVISTE",
        )
        MetroOfferTier.objects.create(offer=old_offer, min_packages=3, price_gross=8)
        MetroOffer.objects.create(
            product=product,
            price_gross=10,
            valid_from=date(2026, 8, 27),
            source="Selenium METRO PUNCT TARGOVISTE",
        )

        response = self.client.get("/app/metro/")

        self.assertEqual(response.context["active_volume_offer_count"], 0)

    @override_settings(METRO_API_ENABLED=True, METRO_SELENIUM_ENABLED=False, METRO_STORE_QUERY="Targoviste")
    @patch("comparator.views.launch_api_catalog_job")
    def test_api_catalog_can_be_started_from_private_ui(self, launcher):
        response = self.client.post("/app/metro/scanari/catalog-api/")

        job = MetroScrapeJob.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(job.scan_type, MetroScrapeJob.ScanType.FULL)
        launcher.assert_called_once_with(job, "Targoviste")

    @override_settings(METRO_API_ENABLED=True, METRO_SELENIUM_ENABLED=False)
    def test_production_ui_hides_and_denies_selenium_actions(self):
        response = self.client.get("/app/metro/scanari/")

        self.assertContains(response, "Actualizare rapidă cu prețuri")
        self.assertNotContains(response, "/app/metro/scanari/porneste/")
        self.assertEqual(
            self.client.post("/app/metro/scanari/extindere-rapida/").status_code,
            403,
        )

    @override_settings(METRO_SELENIUM_ENABLED=True, METRO_STORE_QUERY="Targoviste")
    @patch("comparator.views.launch_breadth_catalog_job")
    def test_fast_expansion_can_be_started_from_private_ui(self, launcher):
        response = self.client.post("/app/metro/scanari/extindere-rapida/")

        job = MetroScrapeJob.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(job.scan_type, MetroScrapeJob.ScanType.TARGETED)
        launcher.assert_called_once_with(job, "Targoviste")

    @override_settings(METRO_SELENIUM_ENABLED=True, METRO_STORE_QUERY="Targoviste")
    @patch("comparator.views.launch_alphabet_catalog_job")
    def test_alphabet_expansion_can_be_started_from_private_ui(self, launcher):
        response = self.client.post("/app/metro/scanari/acoperire-alfabetica/")

        job = MetroScrapeJob.objects.get()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(job.scan_type, MetroScrapeJob.ScanType.TARGETED)
        launcher.assert_called_once_with(job, "Targoviste")

    @patch("comparator.management.commands.metro_seed_catalog.capture_search_terms", return_value=1)
    def test_limited_catalog_scan_is_targeted_and_cannot_mark_products_missing(self, capture):
        call_command(
            "metro_seed_catalog",
            limit_per_search=60,
            no_import=True,
            delay=0.8,
            retries=1,
            store="",
            verbosity=0,
        )

        job = MetroScrapeJob.objects.get()
        self.assertEqual(job.scan_type, MetroScrapeJob.ScanType.TARGETED)
        capture.assert_called_once()

    @patch("comparator.management.commands.metro_seed_catalog.capture_category_catalog", return_value=1)
    def test_category_catalog_scan_is_full_and_uses_taxonomy(self, capture):
        call_command(
            "metro_seed_catalog",
            category_crawl=True,
            no_import=True,
            delay=0.8,
            retries=1,
            store="",
            verbosity=0,
        )

        job = MetroScrapeJob.objects.get()
        self.assertEqual(job.scan_type, MetroScrapeJob.ScanType.FULL)
        capture.assert_called_once()

    @patch("comparator.management.commands.metro_seed_catalog.capture_api_catalog", return_value=1)
    def test_api_catalog_scan_is_full(self, capture):
        call_command(
            "metro_seed_catalog",
            api_crawl=True,
            no_import=True,
            delay=0.3,
            retries=1,
            store="Targoviste",
            verbosity=0,
        )

        job = MetroScrapeJob.objects.get()
        self.assertEqual(job.scan_type, MetroScrapeJob.ScanType.FULL)
        capture.assert_called_once()

    @patch("comparator.management.commands.metro_seed_catalog.capture_search_terms", return_value=1)
    def test_alphabet_catalog_uses_all_letter_pairs_with_safe_limit(self, capture):
        call_command(
            "metro_seed_catalog",
            alphabet_crawl=True,
            no_import=True,
            delay=0.8,
            retries=1,
            store="",
            verbosity=0,
        )

        job = MetroScrapeJob.objects.get()
        terms = capture.call_args.args[1]
        self.assertEqual(job.scan_type, MetroScrapeJob.ScanType.TARGETED)
        self.assertEqual((len(terms), terms[0], terms[-1]), (676, "aa", "zz"))
        self.assertEqual(capture.call_args.kwargs["limit_per_search"], 500)
        self.assertTrue(capture.call_args.kwargs["exact_term_match"])

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

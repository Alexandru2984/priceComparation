from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from comparator.models import BaseUnit, Product, ProductCode
from comparator.services.metro_sitemap import (
    METRO_SITEMAP_URL,
    MetroSitemapError,
    SitemapProduct,
    _locations,
    fetch_metro_sitemap_products,
    import_metro_sitemap_products,
    parse_product_url,
)


class MetroSitemapTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="sitemap-admin",
            password="test-password",
            is_staff=True,
        )
        self.client.force_login(self.user)

    def test_product_url_extracts_identity_measurement_and_category(self):
        product = parse_product_url(
            "https://produse.metro.ro/shop/pv/BTY-X111/0032/0022/"
            "Hochland-Branza-Topita-Triunghi-Smantana-140-g"
        )

        self.assertEqual(product.external_id, "BTY-X111")
        self.assertEqual(product.name, "Hochland Branza Topita Triunghi Smantana 140 g")
        self.assertEqual(product.base_unit, BaseUnit.KILOGRAM)
        self.assertEqual(product.category, "Lactate")

    def test_fetch_follows_only_metro_sitemap_parts_and_deduplicates_codes(self):
        index = b"""<sitemapindex><sitemap><loc>https://produse.metro.ro/searchdiscover/sitemap/part/1</loc></sitemap><sitemap><loc>https://evil.example/sitemap</loc></sitemap></sitemapindex>"""
        part = b"""<urlset><url><loc>https://produse.metro.ro/shop/pv/BTY-1/0032/0021/Lapte-1-L</loc></url><url><loc>https://produse.metro.ro/shop/pv/BTY-1/0032/0022/Lapte-1-L</loc></url></urlset>"""
        session = Mock()

        def response(content):
            result = Mock(content=content)
            result.raise_for_status.return_value = None
            return result

        session.get.side_effect = [response(index), response(part)]

        products = fetch_metro_sitemap_products(session=session)

        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].external_id, "BTY-1")
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(session.get.call_args_list[0].args[0], METRO_SITEMAP_URL)

    def test_sitemap_rejects_xml_entities(self):
        malicious_xml = b"""<!DOCTYPE urlset [
            <!ENTITY secret SYSTEM "file:///etc/passwd">
        ]><urlset><url><loc>&secret;</loc></url></urlset>"""

        with self.assertRaises(MetroSitemapError):
            _locations(malicious_xml)

    def test_import_adds_unknown_codes_without_overwriting_known_product(self):
        known = Product.objects.create(name="Nume verificat din card", base_unit=BaseUnit.PIECE)
        ProductCode.objects.create(product=known, kind=ProductCode.Kind.METRO, code="KNOWN")
        products = [
            SitemapProduct("KNOWN", "Nume sitemap", "https://produse.metro.ro/shop/pv/KNOWN/a/Nume", BaseUnit.PIECE, "Altele"),
            SitemapProduct("NEW", "Lapte Nou 1 L", "https://produse.metro.ro/shop/pv/NEW/a/Lapte-Nou-1-L", BaseUnit.LITER, "Lactate"),
        ]

        stats = import_metro_sitemap_products(products)

        known.refresh_from_db()
        self.assertEqual(known.name, "Nume verificat din card")
        self.assertEqual(stats["new_codes"], 1)
        self.assertEqual(stats["new_products"], 1)
        created = ProductCode.objects.get(code="NEW").product
        self.assertEqual(created.name, "Lapte Nou 1 L")
        self.assertEqual(created.category, "Lactate")

    @patch("comparator.management.commands.metro_import_sitemap.fetch_metro_sitemap_products")
    def test_dry_run_does_not_import(self, fetch):
        fetch.return_value = [
            SitemapProduct("NEW", "Produs", "https://produse.metro.ro/shop/pv/NEW/a/Produs", BaseUnit.PIECE, "Altele")
        ]

        call_command("metro_import_sitemap", "--dry-run")

        self.assertFalse(ProductCode.objects.filter(code="NEW").exists())

    @patch("comparator.views.import_metro_sitemap_products")
    @patch("comparator.views.fetch_metro_sitemap_products")
    def test_sitemap_can_be_synchronized_from_private_ui(self, fetch, import_products):
        fetch.return_value = [Mock()]
        import_products.return_value = {
            "discovered": 10,
            "new_products": 7,
            "new_codes": 8,
            "existing_codes": 2,
        }

        response = self.client.post("/app/metro/scanari/sitemap/")

        self.assertRedirects(response, "/app/metro/scanari/")
        import_products.assert_called_once_with(fetch.return_value)

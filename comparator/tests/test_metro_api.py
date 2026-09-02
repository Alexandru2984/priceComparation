from decimal import Decimal
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from comparator.services.metro_api import (
    MetroApiStore,
    _leaf_categories,
    _measurement,
    normalize_api_details,
    resolve_metro_api_store,
)


class MetroApiTests(SimpleTestCase):
    def test_single_piece_count_keeps_api_content_until_identity_alignment(self):
        measurement = _measurement(
            {
                "bundleSize": "1",
                "basePriceContent": "0.08",
                "basePriceContentMeasureUnit": "kg",
            },
            "Raffaello Praline cu Nuca de Cocos si Migdale 8 bucati 80 g",
        )

        self.assertEqual(
            measurement,
            (Decimal("1"), Decimal("0.08"), "KG", "1 x 0.08 KG"),
        )

    def test_explicit_nested_piece_count_wins_over_api_bundle_size(self):
        measurement = _measurement(
            {"bundleSize": "4"},
            "aro Foi Prosop Interfoliate V 4 X 250 bucati",
        )

        self.assertEqual(
            measurement,
            (Decimal("1000"), Decimal("1"), "BUC", "1000 BUCATI"),
        )

    def test_leaf_categories_keep_live_paths_and_local_category(self):
        payload = {
            "children": {
                "food": {
                    "urlCategoryPath": "alimentare",
                    "amounts": 3,
                    "children": {
                        "milk": {
                            "urlCategoryPath": "alimentare/lactate/lapte",
                            "amounts": 3,
                            "children": {},
                        }
                    },
                }
            }
        }

        self.assertEqual(
            _leaf_categories(payload),
            [{"path": "alimentare/lactate/lapte", "count": 3, "category": "Lactate"}],
        )

    @override_settings(PREFERRED_METRO_STORE="METRO PUNCT TARGOVISTE")
    def test_store_query_resolves_targoviste_point(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "storeInformationMap": {
                "00056": {
                    "city": "Târgoviște",
                    "storeInformationUrl": "https://www.metro.ro/magazinele-noastre/targoviste-punct",
                }
            }
        }
        session = Mock()
        session.get.return_value = response

        store = resolve_metro_api_store("Targoviste", session=session)

        self.assertEqual(store, MetroApiStore("00056", "METRO PUNCT TARGOVISTE"))

    def test_api_details_include_gross_price_package_and_volume_tiers(self):
        price_info = {
            "grossPrice": "41.40",
            "netPrice": "35.26",
            "finalPricesInfo": {
                "articleGross": "35.40",
                "articleNet": "29.26",
                "emptiesGross": "6.00",
            },
            "summaryDnrInfo": {
                "levels": {
                    "1": {"finalSingleGrossPrice": "35.40"},
                    "3": {"finalSingleGrossPrice": "33.48"},
                }
            },
        }
        payload = {
            "result": {
                "BTY-X809291": {
                    "bettyArticleId": {"articleNumber": "BTY-X809291"},
                    "variants": {
                        "0032": {
                            "bettyVariantId": {"bettyVariantId": "BTY-X8092910032"},
                            "bundles": {
                                "0021": {
                                    "description": "2 CAI FRUMOSI Vodca 12 x 0,09 L",
                                    "bundleSize": "12",
                                    "basePriceContent": "0.09",
                                    "basePriceContentMeasureUnit": "l",
                                    "stores": {
                                        "00056": {
                                            "availability": "AVAILABLE",
                                            "sellingPriceInfo": price_info,
                                        }
                                    },
                                }
                            },
                        }
                    },
                }
            }
        }

        rows = normalize_api_details(
            payload,
            {"BTY-X8092910032": Decimal("29.26")},
            MetroApiStore("00056", "METRO PUNCT TARGOVISTE"),
            "Băuturi alcoolice",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price_gross"], Decimal("35.40"))
        self.assertEqual(rows[0]["units_per_package"], Decimal("12"))
        self.assertEqual(rows[0]["unit_size"], Decimal("0.09"))
        self.assertEqual(
            rows[0]["volume_prices"],
            [{"min_packages": 3, "price_gross": "33.48", "label": "33.48 RON pentru 3+"}],
        )

    def test_commercial_name_measurement_wins_over_technical_api_content(self):
        price_info = {
            "grossPrice": "37.80",
            "netPrice": "31.80",
            "finalPricesInfo": {"articleGross": "31.80", "articleNet": "26.72"},
        }
        payload = {
            "result": {
                "BTY-X1": {
                    "variants": {
                        "0032": {
                            "bettyVariantId": {"bettyVariantId": "BTY-X10032"},
                            "bundles": {
                                "0021": {
                                    "description": "Bere Blonda SGR 12 x 0,5 L",
                                    "bundleSize": "12",
                                    "basePriceContent": "0.042",
                                    "basePriceContentMeasureUnit": "l",
                                    "stores": {"00056": {"sellingPriceInfo": price_info}},
                                }
                            },
                        }
                    }
                }
            }
        }

        row = normalize_api_details(
            payload,
            {"BTY-X10032": Decimal("26.72")},
            MetroApiStore("00056", "METRO PUNCT TARGOVISTE"),
            "Băuturi alcoolice",
        )[0]

        self.assertEqual(row["price_gross"], Decimal("31.80"))
        self.assertEqual(row["units_per_package"], Decimal("12"))
        self.assertEqual(row["unit_size"], Decimal("0.5"))

    def test_variable_weight_article_is_priced_per_kilogram(self):
        price_info = {
            "grossPrice": "16.49",
            "netPrice": "14.86",
            "finalPricesInfo": {"articleGross": "16.49", "articleNet": "14.86"},
        }
        payload = {
            "result": {
                "BTY-X2": {
                    "variants": {
                        "0032": {
                            "bettyVariantId": {"bettyVariantId": "BTY-X20032"},
                            "bundles": {
                                "0021": {
                                    "description": "Aripi de Pui cca. 2,5 Kg",
                                    "isWeightArticle": "WEIGHT",
                                    "bundleSize": "1",
                                    "stores": {"00056": {"sellingPriceInfo": price_info}},
                                }
                            },
                        }
                    }
                }
            }
        }

        row = normalize_api_details(
            payload,
            {"BTY-X20032": Decimal("14.86")},
            MetroApiStore("00056", "METRO PUNCT TARGOVISTE"),
            "Carne și pește",
        )[0]

        self.assertEqual(row["units_per_package"], Decimal("1"))
        self.assertEqual(row["unit_size"], Decimal("1"))
        self.assertEqual(row["base_unit"], "KG")

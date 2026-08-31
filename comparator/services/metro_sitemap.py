import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import requests
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from django.db import transaction

from comparator.catalog import infer_category
from comparator.models import Product, ProductCode

from .metro_scraper import parse_measurement


METRO_SITEMAP_URL = (
    "https://produse.metro.ro/searchdiscover/sitemap/country/RO/locale/ro-RO/sitemap"
)
METRO_HOST = "produse.metro.ro"
MAX_SITEMAP_BYTES = 20 * 1024 * 1024


class MetroSitemapError(RuntimeError):
    pass


@dataclass(frozen=True)
class SitemapProduct:
    external_id: str
    name: str
    product_url: str
    base_unit: str
    category: str


def _locations(xml_content):
    if len(xml_content) > MAX_SITEMAP_BYTES:
        raise MetroSitemapError("Fișierul sitemap METRO depășește limita de siguranță.")
    try:
        root = ElementTree.fromstring(xml_content)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise MetroSitemapError("METRO a returnat un sitemap XML invalid.") from exc
    return [
        (node.text or "").strip()
        for node in root.iter()
        if node.tag.endswith("loc") and (node.text or "").strip()
    ]


def _is_allowed_metro_url(url, path_prefix):
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == METRO_HOST
        and parsed.path.startswith(path_prefix)
    )


def parse_product_url(url):
    if not _is_allowed_metro_url(url, "/shop/pv/"):
        return None
    path = urlparse(url).path
    match = re.match(r"^/shop/pv/([^/]+)/(?:[^/]+/)*([^/]+)$", path)
    if not match:
        return None
    external_id = unquote(match.group(1)).strip()[:80]
    slug = unquote(match.group(2)).strip()
    name = re.sub(r"[-_]+", " ", slug)
    name = " ".join(name.split()).strip()[:220]
    if not external_id or not name or not any(character.isalpha() for character in name):
        return None
    _, _, base_unit = parse_measurement(name)
    return SitemapProduct(
        external_id=external_id,
        name=name,
        product_url=url[:1000],
        base_unit=base_unit,
        category=infer_category(name),
    )


def fetch_metro_sitemap_products(session=None):
    client = session or requests.Session()
    headers = {"User-Agent": "PriceMatch/1.0 catalog indexer"}

    def fetch(url):
        try:
            response = client.get(url, headers=headers, timeout=(10, 60))
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            raise MetroSitemapError("Sitemapul METRO nu a putut fi descărcat.") from exc

    part_urls = _locations(fetch(METRO_SITEMAP_URL))
    part_urls = [
        url
        for url in part_urls
        if _is_allowed_metro_url(url, "/searchdiscover/sitemap/")
    ]
    if not part_urls:
        raise MetroSitemapError("Indexul METRO nu conține fișiere sitemap permise.")

    products = {}
    for part_url in part_urls:
        for url in _locations(fetch(part_url)):
            product = parse_product_url(url)
            if product:
                products[product.external_id] = product
    if not products:
        raise MetroSitemapError("Sitemapul METRO nu conține produse valide.")
    return list(products.values())


@transaction.atomic
def import_metro_sitemap_products(products):
    products = list(products)
    existing_codes = set(
        ProductCode.objects.filter(
            kind=ProductCode.Kind.METRO,
            supplier__isnull=True,
        ).values_list("code", flat=True)
    )
    missing = [product for product in products if product.external_id not in existing_codes]
    if not missing:
        return {
            "discovered": len(products),
            "new_products": 0,
            "new_codes": 0,
            "existing_codes": len(products),
        }

    product_keys = {
        (item.name, item.base_unit): item
        for item in missing
    }
    names = {name for name, _ in product_keys}
    catalog_by_key = {
        (product.name, product.base_unit): product
        for product in Product.objects.filter(brand="", name__in=names)
    }
    before_products = Product.objects.count()
    Product.objects.bulk_create(
        [
            Product(
                name=name,
                brand="",
                base_unit=base_unit,
                category=item.category,
            )
            for (name, base_unit), item in product_keys.items()
            if (name, base_unit) not in catalog_by_key
        ],
        batch_size=500,
        ignore_conflicts=True,
    )
    catalog_by_key.update({
        (product.name, product.base_unit): product
        for product in Product.objects.filter(brand="", name__in=names)
    })
    codes_before = ProductCode.objects.filter(
        kind=ProductCode.Kind.METRO,
        supplier__isnull=True,
    ).count()
    ProductCode.objects.bulk_create(
        [
            ProductCode(
                product=catalog_by_key[(item.name, item.base_unit)],
                kind=ProductCode.Kind.METRO,
                code=item.external_id,
            )
            for item in missing
        ],
        batch_size=500,
        ignore_conflicts=True,
    )
    codes_after = ProductCode.objects.filter(
        kind=ProductCode.Kind.METRO,
        supplier__isnull=True,
    ).count()
    new_codes = codes_after - codes_before
    return {
        "discovered": len(products),
        "new_products": Product.objects.count() - before_products,
        "new_codes": new_codes,
        "existing_codes": len(products) - new_codes,
    }

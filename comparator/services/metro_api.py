import time
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone

from comparator.models import BaseUnit, MetroScrapeTerm

from .metro_scraper import (
    _convert_size,
    _decimal,
    _local_category_for_path,
    parse_measurement,
    store_captured_rows,
)


METRO_ORIGIN = "https://produse.metro.ro"
STORE_INFO_URL = f"{METRO_ORIGIN}/cia/content/sitecore/storeinformation/RO/ro-RO"
MAIN_CATEGORIES_URL = f"{METRO_ORIGIN}/searchdiscover/articlesearch/mainCategories"
SEARCH_URL = f"{METRO_ORIGIN}/searchdiscover/articlesearch/search"
DETAIL_URL = f"{METRO_ORIGIN}/evaluate.article.v1/betty-variants"
HEADERS = {"User-Agent": "PriceMatch/1.0 store catalog scanner"}
DETAIL_BATCH_SIZE = 40


class MetroApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class MetroApiStore:
    store_id: str
    name: str


def _plain(value):
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()


def _request_json(session, url, params=None, retries=3):
    last_error = None
    for attempt in range(max(1, retries)):
        try:
            response = session.get(url, params=params, headers=HEADERS, timeout=(10, 60))
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < max(1, retries):
                time.sleep(min(2 ** attempt, 8))
    raise MetroApiError(f"METRO API nu a răspuns corect pentru {url}.") from last_error


def resolve_metro_api_store(store_query, session=None, retries=3):
    client = session or requests.Session()
    payload = _request_json(client, STORE_INFO_URL, retries=retries)
    stores = payload.get("storeInformationMap", {})
    query = _plain(store_query).strip()
    matches = []
    for store_id, data in stores.items():
        searchable = _plain(
            " ".join((store_id, data.get("city", ""), data.get("storeInformationUrl", "")))
        )
        if query == store_id or (query and all(token in searchable for token in query.split())):
            matches.append((store_id, data))
    if len(matches) != 1:
        raise MetroApiError(
            f"Magazinul METRO «{store_query}» nu a putut fi identificat fără ambiguitate."
        )
    store_id, data = matches[0]
    city = _plain(data.get("city", "")).upper()
    preferred = settings.PREFERRED_METRO_STORE.strip()
    if preferred and query and all(token in _plain(preferred) for token in query.split()):
        name = preferred
    elif "-punct" in data.get("storeInformationUrl", ""):
        name = f"METRO PUNCT {city}"
    else:
        name = f"METRO {city}"
    return MetroApiStore(store_id=store_id, name=name[:120])


def _leaf_categories(payload):
    leaves = []

    def visit(node):
        children = node.get("children") or {}
        if children:
            for child in children.values():
                visit(child)
            return
        path = (node.get("urlCategoryPath") or "").strip("/")
        if path and int(node.get("amounts") or 0) > 0:
            leaves.append({
                "path": path,
                "count": int(node.get("amounts") or 0),
                "category": _local_category_for_path(f"/shop/category/{path}"),
            })

    for root in (payload.get("children") or {}).values():
        visit(root)
    return sorted(leaves, key=lambda item: item["path"])


def fetch_metro_api_categories(store_id, session=None, retries=3):
    client = session or requests.Session()
    payload = _request_json(
        client,
        MAIN_CATEGORIES_URL,
        params={"storeId": store_id, "language": "ro-RO", "country": "RO"},
        retries=retries,
    )
    categories = _leaf_categories(payload)
    if not categories:
        raise MetroApiError("METRO API nu a returnat categorii pentru magazinul selectat.")
    return categories


def _category_variant_prices(session, store_id, path, retries):
    variants = {}
    page = 1
    while True:
        payload = _request_json(
            session,
            SEARCH_URL,
            params={
                "storeId": store_id,
                "language": "ro-RO",
                "country": "RO",
                "query": "*",
                "rows": 1000,
                "page": page,
                "filter": f"category:{path}",
                "facets": "false",
                "categories": "false",
            },
            retries=retries,
        )
        for variant_id in payload.get("resultIds", []):
            result = (payload.get("results") or {}).get(variant_id, {})
            if result.get("isAvailable", True):
                variants[variant_id] = _decimal(result.get("price"))
        next_page = payload.get("nextPage")
        if not next_page:
            break
        if int(next_page) <= page or int(next_page) > 100:
            raise MetroApiError(f"Paginare METRO invalidă pentru categoria {path}.")
        page = int(next_page)
    return variants


def _volume_prices(price_info):
    levels = ((price_info.get("summaryDnrInfo") or {}).get("levels") or {})
    tiers = []
    for minimum, level in levels.items():
        minimum_value = _decimal(minimum)
        gross = _decimal((level or {}).get("finalSingleGrossPrice"))
        if minimum_value is None or gross is None or minimum_value < 2:
            continue
        min_packages = int(minimum_value)
        tiers.append({
            "min_packages": min_packages,
            "price_gross": f"{gross:.2f}",
            "label": f"{gross:.2f} RON pentru {min_packages}+",
        })
    return sorted(tiers, key=lambda tier: tier["min_packages"])


def _measurement(bundle, name):
    if bundle.get("isWeightArticle") == "WEIGHT":
        return Decimal("1"), Decimal("1"), BaseUnit.KILOGRAM, "1 KILOGRAM"
    units, unit_size, base_unit = parse_measurement(name)
    content = _decimal(bundle.get("basePriceContent"))
    measure_unit = (bundle.get("basePriceContentMeasureUnit") or "").lower()
    bundle_size = _decimal(bundle.get("bundleSize"))
    if base_unit != BaseUnit.PIECE:
        package_text = f"{units} x {unit_size} {base_unit}"
        return units, unit_size, base_unit, package_text
    if content is not None and measure_unit in {"g", "gr", "kg", "ml", "l"}:
        unit_size, base_unit = _convert_size(content, measure_unit)
        units = bundle_size or units
    elif bundle_size and bundle_size > 1 and base_unit == BaseUnit.PIECE:
        units = bundle_size
    if content is not None and measure_unit:
        package_text = f"{bundle_size or 1} x {content} {measure_unit.upper()}"
    else:
        package_text = f"{bundle_size or units} BUCATI"
    return units, unit_size, base_unit, package_text


def normalize_api_details(payload, requested_prices, store, category):
    rows = {}
    requested_ids = set(requested_prices)
    for article_key, article in (payload.get("result") or {}).items():
        external_id = (
            (article.get("bettyArticleId") or {}).get("articleNumber") or article_key
        )[:80]
        for variant_number, variant in (article.get("variants") or {}).items():
            variant_id = (
                (variant.get("bettyVariantId") or {}).get("bettyVariantId")
                or f"{external_id}{variant_number}"
            )
            if variant_id not in requested_ids:
                continue
            candidates = []
            for bundle_number, bundle in (variant.get("bundles") or {}).items():
                store_data = (bundle.get("stores") or {}).get(store.store_id) or {}
                price_info = store_data.get("sellingPriceInfo") or {}
                product_prices = price_info.get("finalPricesInfo") or {}
                # METRO's top-level gross/net values include refundable SGR
                # deposits. Product comparisons must use the article itself.
                gross = _decimal(product_prices.get("articleGross")) or _decimal(
                    price_info.get("grossPrice")
                )
                net = _decimal(product_prices.get("articleNet")) or _decimal(
                    price_info.get("netPrice")
                )
                if gross is None or gross <= 0 or store_data.get("availability") == "UNAVAILABLE":
                    continue
                expected_net = requested_prices.get(variant_id)
                difference = abs(net - expected_net) if net is not None and expected_net is not None else Decimal("0")
                candidates.append((difference, bundle_number, bundle, price_info, gross))
            if not candidates:
                continue
            _, bundle_number, bundle, price_info, gross = min(candidates, key=lambda item: (item[0], item[1]))
            name = " ".join(
                (bundle.get("description") or variant.get("description") or article.get("description") or "").split()
            )[:240]
            if not name:
                continue
            units, size, base_unit, package_text = _measurement(bundle, name)
            slug = quote(name.replace(" ", "-"), safe="-+")
            rows[external_id] = {
                "external_id": external_id,
                "name": name,
                "product_url": (
                    f"{METRO_ORIGIN}/shop/pv/{external_id}/{variant_number}/{bundle_number}/{slug}"
                )[:1000],
                "store_name": store.name,
                "package_text": package_text[:120],
                "units_per_package": units,
                "unit_size": size,
                "base_unit": base_unit,
                "category": category,
                "price_gross": gross,
                "volume_prices": _volume_prices(price_info),
            }
    return list(rows.values())


def capture_api_catalog(
    job,
    store_query,
    delay_seconds=0.3,
    progress=None,
    retries=3,
    refresh_completed=False,
    session=None,
):
    client = session or requests.Session()
    store = resolve_metro_api_store(store_query, session=client, retries=retries)
    categories = fetch_metro_api_categories(store.store_id, session=client, retries=retries)
    job.store_name = store.name
    job.save(update_fields=["store_name"])
    for category in categories:
        MetroScrapeTerm.objects.get_or_create(
            job=job,
            term=f"/shop/category/{category['path']}",
            defaults={"category": category["category"]},
        )
    job.total_queries = job.terms.count()
    job.completed_queries = job.terms.filter(status=MetroScrapeTerm.Status.COMPLETED).count()
    job.save(update_fields=["total_queries", "completed_queries"])

    terms = job.terms.all()
    if not refresh_completed:
        terms = terms.exclude(status=MetroScrapeTerm.Status.COMPLETED)
    for term in terms:
        term.status = MetroScrapeTerm.Status.RUNNING
        term.attempts += 1
        term.started_at = timezone.now()
        term.error = ""
        term.save(update_fields=["status", "attempts", "started_at", "error"])
        path = term.term.removeprefix("/shop/category/")
        try:
            requested = _category_variant_prices(client, store.store_id, path, retries)
            found = 0
            items = list(requested.items())
            for offset in range(0, len(items), DETAIL_BATCH_SIZE):
                batch = dict(items[offset:offset + DETAIL_BATCH_SIZE])
                params = [
                    ("storeIds", store.store_id),
                    *(("ids", variant_id) for variant_id in batch),
                    ("country", "RO"),
                    ("locale", "ro-RO"),
                ]
                payload = _request_json(client, DETAIL_URL, params=params, retries=retries)
                rows = normalize_api_details(payload, batch, store, term.category)
                store_captured_rows(job, rows)
                found += len(rows)
            term.status = MetroScrapeTerm.Status.COMPLETED
            term.found_count = found
            term.finished_at = timezone.now()
            term.error = ""
            term.save(update_fields=["status", "found_count", "finished_at", "error"])
        except MetroApiError as exc:
            term.status = MetroScrapeTerm.Status.ERROR
            term.error = str(exc)[:2000]
            term.finished_at = timezone.now()
            term.save(update_fields=["status", "error", "finished_at"])
        job.completed_queries = job.terms.filter(status=MetroScrapeTerm.Status.COMPLETED).count()
        job.save(update_fields=["completed_queries"])
        if progress:
            progress(job.completed_queries, job.total_queries, term.term, job.captured_count)
        time.sleep(delay_seconds)
    return job.captured_count

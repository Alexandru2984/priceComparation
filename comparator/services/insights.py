from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max
from django.utils import timezone

from comparator.models import MetroOffer, Product, SupplierOffer
from .matching import normalize_name


def _catalog_cache_key(prefix):
    product_state = Product.objects.filter(active=True).aggregate(total=Count("id"), latest=Max("updated_at"))
    offer_state = MetroOffer.objects.aggregate(total=Count("id"), latest=Max("updated_at"))
    product_latest = product_state["latest"].isoformat() if product_state["latest"] else "none"
    offer_latest = offer_state["latest"].isoformat() if offer_state["latest"] else "none"
    return (
        f"pricematch:{prefix}:{product_state['total']}:{product_latest}:"
        f"{offer_state['total']}:{offer_latest}"
    )


def current_source_options(product, quantity=None):
    options = []
    metro = product.current_metro_offer(quantity)
    if metro:
        package_count = metro.package_count_for_quantity(quantity)
        package_price = metro.price_for_packages(package_count)
        options.append(
            {
                "kind": "METRO",
                "source": metro.source,
                "price": package_price / metro.total_base_quantity,
                "package_count": package_count,
                "volume_applied": package_price != metro.price_gross,
                "total": package_count * package_price if quantity is not None else None,
                "valid_from": metro.valid_from,
            }
        )

    cutoff = timezone.localdate() - timedelta(days=settings.SUPPLIER_PRICE_MAX_AGE_DAYS)
    offers = product.supplier_offers.select_related("supplier").filter(valid_from__gte=cutoff).order_by(
        "supplier_id", "-valid_from", "price_per_base_unit"
    )
    seen = set()
    for offer in offers:
        if offer.supplier_id in seen or offer.supplier.is_metro:
            continue
        seen.add(offer.supplier_id)
        options.append(
            {
                "kind": "SUPPLIER",
                "source": offer.supplier.name,
                "price": offer.price_per_base_unit,
                "package_count": None,
                "volume_applied": False,
                "total": offer.price_per_base_unit * quantity if quantity is not None else None,
                "valid_from": offer.valid_from,
            }
        )
    return sorted(
        options,
        key=lambda item: (
            item["total"] if item["total"] is not None else item["price"],
            -item["valid_from"].toordinal(),
        ),
    )


def shopping_recommendation(item):
    options = current_source_options(item.product, item.quantity)
    if not options:
        return {"best": None, "options": [], "total": None, "saving": None}
    best = options[0]
    worst = options[-1]
    return {
        "best": best,
        "options": options,
        "total": best["total"],
        "saving": worst["total"] - best["total"] if len(options) > 1 else Decimal("0"),
    }


def product_history(product):
    rows = []
    for offer in product.metro_offers.all():
        rows.append(
            {
                "date": offer.valid_from,
                "source": offer.source,
                "price": offer.price_per_base_unit,
                "kind": "METRO",
            }
        )
    for offer in product.supplier_offers.select_related("supplier"):
        rows.append(
            {
                "date": offer.valid_from,
                "source": offer.supplier.name,
                "price": offer.price_per_base_unit,
                "kind": "Furnizor",
            }
        )
    rows.sort(key=lambda item: (item["date"], item["source"]), reverse=True)
    prices = [item["price"] for item in rows]
    maximum = max(prices, default=Decimal("0"))
    minimum = min(prices, default=Decimal("0"))
    for item in rows:
        item["bar_percent"] = round(item["price"] / maximum * 100) if maximum else 0
    return rows, minimum, maximum


def recent_metro_changes(limit=8):
    cache_key = f"{_catalog_cache_key('metro-changes')}:{limit}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    grouped = defaultdict(list)
    offers = MetroOffer.objects.select_related("product").filter(active=True).order_by(
        "product_id", "source", "-valid_from", "-created_at"
    )
    for offer in offers:
        key = (offer.product_id, offer.source)
        if len(grouped[key]) < 2:
            grouped[key].append(offer)
    changes = []
    for pair in grouped.values():
        if len(pair) < 2 or not pair[1].price_per_base_unit:
            continue
        percent = (pair[0].price_per_base_unit - pair[1].price_per_base_unit) / pair[1].price_per_base_unit * 100
        if abs(percent) >= Decimal("0.1"):
            changes.append({"offer": pair[0], "previous": pair[1], "percent": percent})
    changes.sort(key=lambda item: abs(item["percent"]), reverse=True)
    result = changes[:limit]
    cache.set(cache_key, result, 300)
    return result


def catalog_quality_summary():
    cache_key = _catalog_cache_key("catalog-quality")
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    products = list(Product.objects.filter(active=True).prefetch_related("metro_offers"))
    normalized = defaultdict(list)
    missing_prices = 0
    suspicious_prices = 0
    missing_categories = 0
    for product in products:
        normalized[(normalize_name(product.name), product.base_unit)].append(product)
        if not product.current_metro_offer():
            missing_prices += 1
        if not product.category or product.category == "Altele":
            missing_categories += 1
        suspicious_prices += sum(
            1 for offer in product.metro_offers.all()
            if offer.price_per_base_unit <= 0 or offer.price_per_base_unit > Decimal("10000")
        )
    duplicates = sum(len(group) - 1 for group in normalized.values() if len(group) > 1)
    result = {
        "duplicates": duplicates,
        "missing_prices": missing_prices,
        "missing_categories": missing_categories,
        "suspicious_prices": suspicious_prices,
        "total": duplicates + missing_prices + missing_categories + suspicious_prices,
    }
    cache.set(cache_key, result, 300)
    return result

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Count, Max
from django.utils import timezone

from comparator.models import InvoiceLine, MetroOffer, Product, SupplierOffer
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
    offers = product.supplier_offers.select_related("supplier", "invoice_line").filter(valid_from__gte=cutoff).order_by(
        "supplier_id", "-valid_from", "price_per_base_unit"
    )
    seen = set()
    for offer in offers:
        if offer.supplier_id in seen or offer.supplier.is_metro:
            continue
        seen.add(offer.supplier_id)
        package_quantity = offer.invoice_line.units_per_package * offer.invoice_line.unit_size
        package_count = (
            max(1, int((quantity / package_quantity).to_integral_value(rounding=ROUND_CEILING)))
            if quantity is not None and package_quantity
            else None
        )
        package_price = offer.price_per_base_unit * package_quantity
        options.append(
            {
                "kind": "SUPPLIER",
                "source": offer.supplier.name,
                "price": offer.price_per_base_unit,
                "package_count": package_count,
                "package_quantity": package_quantity,
                "package_price": package_price,
                "volume_applied": False,
                "total": package_count * package_price if package_count is not None else None,
                "supplier_id": offer.supplier_id,
                "supplier": offer.supplier,
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


def profitability_analysis(inventory_item):
    options = current_source_options(inventory_item.product)
    best = options[0] if options else None
    retail_gross = inventory_item.retail_price_gross
    result = {
        "source": best["source"] if best else None,
        "cost_per_base_gross": best["price"] if best else None,
        "cost_per_sale_gross": None,
        "cost_per_sale_net": None,
        "effective_cost_net": None,
        "retail_gross": retail_gross,
        "retail_net": None,
        "margin_amount_net": None,
        "margin_percent": None,
        "recommended_retail_gross": None,
        "status": "INCOMPLETE",
    }
    if not best or retail_gross is None:
        return result

    hundred = Decimal("100")
    cost_gross = best["price"] * inventory_item.retail_unit_size
    purchase_vat_divisor = Decimal("1") + inventory_item.purchase_vat_rate / hundred
    retail_vat_divisor = Decimal("1") + inventory_item.retail_vat_rate / hundred
    sellable_fraction = Decimal("1") - inventory_item.expected_waste_percent / hundred
    target_fraction = Decimal("1") - inventory_item.target_margin_percent / hundred
    cost_net = cost_gross / purchase_vat_divisor
    effective_cost_net = cost_net / sellable_fraction
    retail_net = retail_gross / retail_vat_divisor
    margin_amount = retail_net - effective_cost_net
    margin_percent = margin_amount / retail_net * hundred if retail_net else Decimal("0")
    recommended_net = effective_cost_net / target_fraction
    recommended_gross = recommended_net * retail_vat_divisor

    result.update(
        {
            "cost_per_sale_gross": cost_gross,
            "cost_per_sale_net": cost_net,
            "effective_cost_net": effective_cost_net,
            "retail_net": retail_net,
            "margin_amount_net": margin_amount,
            "margin_percent": margin_percent,
            "recommended_retail_gross": recommended_gross,
            "status": (
                "LOSS"
                if margin_amount < 0
                else "BELOW_TARGET"
                if margin_percent < inventory_item.target_margin_percent
                else "ON_TARGET"
            ),
        }
    )
    return result


def profitability_summary(items):
    rows = [(item, profitability_analysis(item)) for item in items]
    complete = [analysis for _, analysis in rows if analysis["margin_percent"] is not None]
    return {
        "rows": rows,
        "configured_count": len(complete),
        "incomplete_count": len(rows) - len(complete),
        "loss_count": sum(analysis["status"] == "LOSS" for analysis in complete),
        "below_target_count": sum(analysis["status"] == "BELOW_TARGET" for analysis in complete),
        "average_margin": (
            sum((analysis["margin_percent"] for analysis in complete), Decimal("0")) / len(complete)
            if complete
            else None
        ),
    }


def _build_source_orders(rows):
    orders = {}
    for result in rows.values():
        option = result["best"]
        if not option:
            continue
        key = f"supplier:{option['supplier_id']}" if option.get("supplier_id") else f"source:{option['source']}"
        order = orders.setdefault(
            key,
            {
                "source": option["source"],
                "supplier": option.get("supplier"),
                "subtotal": Decimal("0"),
                "transport": Decimal("0"),
                "total": Decimal("0"),
                "below_minimum": False,
                "item_count": 0,
            },
        )
        order["subtotal"] += option["total"]
        order["item_count"] += 1
    for order in orders.values():
        supplier = order["supplier"]
        if supplier:
            order["transport"] = supplier.delivery_cost_for(order["subtotal"])
            order["below_minimum"] = order["subtotal"] < supplier.minimum_order_gross
        order["total"] = order["subtotal"] + order["transport"]
    return list(orders.values())


def _orders_total(orders):
    return sum((order["total"] for order in orders), Decimal("0"))


def _optimize_assignments(items):
    rows = {}
    for item in items:
        options = current_source_options(item.product, item.quantity)
        rows[item.pk] = {"options": options, "best": options[0] if options else None}

    invalid_suppliers = set()
    for _ in range(5):
        orders = _build_source_orders(rows)
        newly_invalid = {
            order["supplier"].pk
            for order in orders
            if order["supplier"] and order["below_minimum"]
        }
        if not newly_invalid - invalid_suppliers:
            invalid_suppliers |= newly_invalid
            break
        invalid_suppliers |= newly_invalid
        for result in rows.values():
            current = result["best"]
            if current and current.get("supplier_id") in invalid_suppliers:
                replacement = next(
                    (
                        option for option in result["options"]
                        if option.get("supplier_id") not in invalid_suppliers
                    ),
                    None,
                )
                if replacement:
                    result["best"] = replacement

    # Transportul se plătește o singură dată pe comandă. Pornim de la cele mai
    # ieftine linii și căutăm mutări care reduc costul întregului coș.
    for _ in range(max(len(rows) * 2, 1)):
        current_orders = _build_source_orders(rows)
        current_total = _orders_total(current_orders)
        best_move = None
        best_total = current_total
        for item_id, result in rows.items():
            original = result["best"]
            for option in result["options"]:
                if option is original or option.get("supplier_id") in invalid_suppliers:
                    continue
                result["best"] = option
                candidate_orders = _build_source_orders(rows)
                if any(order["below_minimum"] for order in candidate_orders):
                    continue
                candidate_total = _orders_total(candidate_orders)
                if candidate_total < best_total:
                    best_total = candidate_total
                    best_move = (item_id, option)
            result["best"] = original
        if best_move is None:
            break
        rows[best_move[0]]["best"] = best_move[1]

    return rows, _build_source_orders(rows)


def optimize_shopping_list(shopping_list):
    items = list(
        shopping_list.items.select_related("product").prefetch_related(
            "product__metro_offers__volume_tiers",
            "product__supplier_offers__supplier",
            "product__supplier_offers__invoice_line",
        )
    )
    active_items = [item for item in items if not item.purchased]
    rows, orders = _optimize_assignments(active_items)
    for item in items:
        if item.pk not in rows:
            options = current_source_options(item.product, item.quantity)
            rows[item.pk] = {"options": options, "best": options[0] if options else None}
    total = _orders_total(orders)
    deferred_ids = set()
    budget = shopping_list.budget_gross
    if budget is not None and total > budget:
        defer_order = sorted(
            active_items,
            key=lambda item: (
                -item.priority,
                -(rows[item.pk]["best"]["total"] if rows[item.pk]["best"] else Decimal("0")),
            ),
        )
        for deferred in defer_order:
            deferred_ids.add(deferred.pk)
            remaining = [item for item in active_items if item.pk not in deferred_ids]
            remaining_rows, remaining_orders = _optimize_assignments(remaining)
            remaining_total = _orders_total(remaining_orders)
            if remaining_total <= budget:
                for item in remaining:
                    rows[item.pk] = remaining_rows[item.pk]
                orders, total = remaining_orders, remaining_total
                break
    output_rows = []
    potential_saving = Decimal("0")
    for item in items:
        result = rows[item.pk]
        result["deferred"] = item.pk in deferred_ids
        result["total"] = (
            result["best"]["total"]
            if result["best"] and not result["deferred"] and not item.purchased
            else None
        )
        if len(result["options"]) > 1 and not result["deferred"] and not item.purchased:
            result["saving"] = result["options"][-1]["total"] - result["best"]["total"]
            potential_saving += result["saving"]
        else:
            result["saving"] = Decimal("0") if result["best"] else None
        output_rows.append((item, result))
    return {
        "rows": output_rows,
        "orders": orders,
        "total": total,
        "potential_saving": potential_saving,
        "budget": budget,
        "budget_remaining": budget - total if budget is not None else None,
        "deferred_count": len(deferred_ids),
        "has_minimum_warnings": any(order["below_minimum"] for order in orders),
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


def matching_quality_summary():
    lines = InvoiceLine.objects.all()
    confirmed = lines.filter(needs_review=False)
    evaluated = confirmed.exclude(match_method=InvoiceLine.MatchMethod.NONE)
    corrected = evaluated.filter(match_corrected=True).count()
    evaluated_count = evaluated.count()
    aggregates = lines.aggregate(total=Count("id"), average_score=Avg("match_score"))
    return {
        "total": aggregates["total"],
        "average_score": aggregates["average_score"] or Decimal("0"),
        "needs_review": lines.filter(needs_review=True).count(),
        "unmatched": lines.filter(matched_product__isnull=True).count(),
        "confirmed": confirmed.count(),
        "exact": lines.filter(
            match_method__in=[InvoiceLine.MatchMethod.CODE, InvoiceLine.MatchMethod.ALIAS]
        ).count(),
        "fuzzy": lines.filter(match_method=InvoiceLine.MatchMethod.FUZZY).count(),
        "corrected": corrected,
        "observed_precision": (
            Decimal(evaluated_count - corrected) / Decimal(evaluated_count) * 100
            if evaluated_count
            else None
        ),
    }

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import models
from rapidfuzz import fuzz

from comparator.models import BaseUnit, InvoiceLine, Product, ProductAlias, ProductCode


STOPWORDS = {
    "buc", "bucati", "bucata", "cutie", "cutii", "bax", "baxuri", "pet", "punga",
    "sticla", "kg", "g", "gr", "l", "litri", "ml", "x", "folie", "imp",
}


def _ascii(value):
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii").lower()


def normalize_name(value):
    """Normalize text without corrupting quantities such as 500 g or 10 x 2 L."""
    tokens = re.findall(r"[a-z]+|\d+(?:[.,]\d+)?", _ascii(value))
    return " ".join(token.replace(",", ".") for token in tokens if token not in STOPWORDS)


def _decimal(value):
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _convert_measure(value, unit):
    size = _decimal(value) or Decimal("1")
    unit = unit.lower()
    if unit in {"g", "gr"}:
        return size / Decimal("1000"), BaseUnit.KILOGRAM
    if unit == "kg":
        return size, BaseUnit.KILOGRAM
    if unit == "ml":
        return size / Decimal("1000"), BaseUnit.LITER
    return size, BaseUnit.LITER


def measurement_signature(value):
    """Return unit size, base unit and package count inferred from a commercial name."""
    text = _ascii(value).replace(",", ".")
    multi = re.search(r"\b(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(kg|gr?|g|ml|l)\b", text)
    if multi:
        count = _decimal(multi.group(1)) or Decimal("1")
        size, unit = _convert_measure(multi.group(2), multi.group(3))
        return {"unit_size": size, "base_unit": unit, "package_count": count}
    measures = list(re.finditer(r"\b(\d+(?:\.\d+)?)\s*(kg|gr?|g|ml|l)\b", text))
    if measures:
        size, unit = _convert_measure(measures[-1].group(1), measures[-1].group(2))
        return {"unit_size": size, "base_unit": unit, "package_count": None}
    pieces = re.search(r"\b(\d+(?:\.\d+)?)\s*buc(?:ata|ati)?\b", text)
    if pieces:
        return {
            "unit_size": Decimal("1"),
            "base_unit": BaseUnit.PIECE,
            "package_count": _decimal(pieces.group(1)),
        }
    return None


def _package_adjustment(requested, candidate):
    if not requested or not candidate:
        return 0, "gramaj necunoscut"
    if requested["base_unit"] != candidate["base_unit"]:
        return -35, "unitate diferită"
    left = requested["unit_size"]
    right = candidate["unit_size"]
    if not left or not right:
        return 0, "gramaj necunoscut"
    ratio = max(left, right) / min(left, right)
    if ratio <= Decimal("1.02"):
        adjustment, reason = 10, "același gramaj"
    elif ratio <= Decimal("1.15"):
        adjustment, reason = 2, "gramaj apropiat"
    elif ratio >= Decimal("1.5"):
        adjustment, reason = -25, "gramaj diferit"
    else:
        adjustment, reason = -8, "gramaj posibil diferit"
    if (
        requested["base_unit"] == BaseUnit.PIECE
        and requested.get("package_count")
        and candidate.get("package_count")
    ):
        adjustment += 5 if requested["package_count"] == candidate["package_count"] else -10
    return adjustment, reason


def _requested_signature(name, base_unit=None, unit_size=None, units_per_package=None):
    if unit_size is not None and base_unit:
        return {
            "unit_size": Decimal(str(unit_size)),
            "base_unit": base_unit,
            "package_count": Decimal(str(units_per_package)) if units_per_package is not None else None,
        }
    return measurement_signature(name)


def _exact_result(product, method, reason):
    return [{
        "product": product,
        "product_id": product.pk,
        "name": product.name,
        "brand": product.brand,
        "score": 100,
        "lexical_score": 100,
        "reason": reason,
        "method": method,
    }]


def rank_product_candidates(
    name,
    supplier=None,
    base_unit=None,
    code="",
    unit_size=None,
    units_per_package=None,
    limit=3,
):
    code = (code or "").strip().upper()
    if code:
        direct = Product.objects.filter(ean__iexact=code, active=True).first()
        if direct and (not base_unit or direct.base_unit == base_unit):
            return _exact_result(direct, InvoiceLine.MatchMethod.CODE, "EAN/GTIN exact")
        codes = ProductCode.objects.select_related("product").filter(code__iexact=code, product__active=True)
        if supplier:
            codes = codes.filter(models.Q(supplier=supplier) | models.Q(supplier__isnull=True))
        direct_code = codes.first()
        if direct_code and (not base_unit or direct_code.product.base_unit == base_unit):
            return _exact_result(direct_code.product, InvoiceLine.MatchMethod.CODE, "cod furnizor exact")

    normalized = normalize_name(name)
    if not normalized:
        return []
    aliases = ProductAlias.objects.select_related("product")
    if supplier:
        aliases = aliases.filter(supplier=supplier)
    for alias in aliases:
        if normalize_name(alias.alias) == normalized and (not base_unit or alias.product.base_unit == base_unit):
            return _exact_result(alias.product, InvoiceLine.MatchMethod.ALIAS, "corecție învățată")

    requested = _requested_signature(name, base_unit, unit_size, units_per_package)
    products = Product.objects.filter(active=True)
    if base_unit:
        products = products.filter(base_unit=base_unit)
    ranked = []
    for product in products.only("id", "name", "brand", "base_unit"):
        candidate_text = normalize_name(f"{product.brand} {product.name}")
        weighted = fuzz.WRatio(normalized, candidate_text)
        tokens = fuzz.token_set_ratio(normalized, candidate_text)
        lexical = round(weighted * 0.7 + tokens * 0.3)
        adjustment, package_reason = _package_adjustment(requested, measurement_signature(product.name))
        score = max(0, min(100, lexical + adjustment))
        ranked.append(
            {
                "product": product,
                "product_id": product.pk,
                "name": product.name,
                "brand": product.brand,
                "score": score,
                "lexical_score": lexical,
                "reason": package_reason,
                "method": InvoiceLine.MatchMethod.FUZZY,
            }
        )
    ranked.sort(key=lambda item: (-item["score"], -item["lexical_score"], item["name"]))
    return ranked[:limit]


def suggest_product(name, supplier=None, base_unit=None, code="", unit_size=None, units_per_package=None):
    candidates = rank_product_candidates(
        name,
        supplier=supplier,
        base_unit=base_unit,
        code=code,
        unit_size=unit_size,
        units_per_package=units_per_package,
        limit=2,
    )
    return (candidates[0]["product"], candidates[0]["score"]) if candidates else (None, 0)


def apply_match(line):
    candidates = rank_product_candidates(
        line.original_name,
        supplier=line.invoice.supplier,
        base_unit=line.base_unit,
        code=getattr(line, "ean", ""),
        unit_size=line.unit_size,
        units_per_package=line.units_per_package,
    )
    if not candidates:
        line.match_score = 0
        line.match_gap = 0
        line.match_method = InvoiceLine.MatchMethod.NONE
        line.match_candidates = []
        line.needs_review = True
        return line
    best = candidates[0]
    second_score = candidates[1]["score"] if len(candidates) > 1 else 0
    gap = max(0, best["score"] - second_score)
    line.match_score = best["score"]
    line.match_gap = gap
    line.match_method = best["method"]
    line.match_candidates = [
        {
            "product_id": item["product_id"],
            "name": item["name"],
            "brand": item["brand"],
            "score": item["score"],
            "reason": item["reason"],
        }
        for item in candidates
    ]
    exact = best["method"] in {InvoiceLine.MatchMethod.CODE, InvoiceLine.MatchMethod.ALIAS}
    if best["score"] >= settings.MATCH_REVIEW_THRESHOLD:
        line.matched_product = best["product"]
    line.needs_review = not exact and (
        best["score"] < settings.MATCH_AUTO_THRESHOLD
        or gap < settings.MATCH_AMBIGUITY_GAP
    )
    return line

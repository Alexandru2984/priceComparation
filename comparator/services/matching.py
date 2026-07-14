import re
import unicodedata

from django.conf import settings
from rapidfuzz import fuzz

from comparator.models import Product, ProductAlias


STOPWORDS = {
    "buc", "bucati", "bucata", "cutie", "cutii", "bax", "pet", "punga", "sticla",
    "kg", "g", "gr", "l", "litri", "ml", "x",
}


def normalize_name(value):
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
    value = value.lower().replace("0", "o")
    tokens = re.findall(r"[a-z]+|\d+(?:[.,]\d+)?", value)
    return " ".join(token for token in tokens if token not in STOPWORDS)


def suggest_product(name, supplier=None, base_unit=None):
    normalized = normalize_name(name)
    if not normalized:
        return None, 0

    aliases = ProductAlias.objects.select_related("product")
    if supplier:
        aliases = aliases.filter(supplier=supplier)
    for alias in aliases:
        if normalize_name(alias.alias) == normalized and (not base_unit or alias.product.base_unit == base_unit):
            return alias.product, 100

    best_product = None
    best_score = 0
    products = Product.objects.filter(active=True)
    if base_unit:
        products = products.filter(base_unit=base_unit)
    for product in products:
        candidate = normalize_name(f"{product.brand} {product.name}")
        score = round(fuzz.WRatio(normalized, candidate))
        if score > best_score:
            best_product, best_score = product, score
    return best_product, best_score


def apply_match(line):
    product, score = suggest_product(line.original_name, line.invoice.supplier, line.base_unit)
    line.match_score = score
    if product and score >= settings.MATCH_REVIEW_THRESHOLD:
        line.matched_product = product
    line.needs_review = not product or score < settings.MATCH_AUTO_THRESHOLD
    return line

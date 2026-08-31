import json
import re
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from rapidfuzz import fuzz

from comparator.models import BaseUnit

PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "original_name": {"type": "string"},
                    "ean": {"type": "string"},
                    "quantity": {"type": "number"},
                    "units_per_package": {"type": "number"},
                    "unit_size": {"type": "number"},
                    "base_unit": {"type": "string", "enum": ["BUC", "KG", "L"]},
                    "unit_price_gross": {"type": "number"},
                    "vat_rate": {"type": "number"},
                    "line_total_gross": {"type": ["number", "null"]},
                    "discount_gross": {"type": "number"},
                    "deposit_gross": {"type": "number"},
                },
                "required": [
                    "original_name", "ean", "quantity", "units_per_package", "unit_size", "base_unit",
                    "unit_price_gross", "vat_rate", "line_total_gross", "discount_gross", "deposit_gross",
                ],
            },
        }
    },
    "required": ["products"],
}


def _decimal(value, default="0"):
    try:
        return Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, AttributeError, ValueError):
        return Decimal(default)


def parse_with_ollama(text):
    prompt = f"""Extrage produsele din textul OCR al unei facturi sau al unui bon fiscal românesc.
Nu inventa valori. quantity este numărul de pachete cumpărate; units_per_package este numărul de
bucăți dintr-un bax; unit_size este cantitatea unei bucăți exprimată în KG, L sau 1 pentru BUC.
unit_price_gross este prețul cu TVA al unui pachet. Dacă nu există TVA, folosește 0.
ean este codul de bare sau codul produsului numai dacă apare explicit. Separă reducerea și garanția
SGR în discount_gross și deposit_gross; nu le inventa și nu le trata ca produse când apar clar lângă o linie.
În expresia «10 bucăți x 7,90 RON», 10 este quantity, 7,90 este unit_price_gross,
iar units_per_package este 1. Folosește o valoare mai mare de 1 pentru units_per_package doar dacă
textul menționează explicit un bax sau o ambalare de tip «6 x 2 L».
Ignoră subtotaluri, totaluri, datele firmei și liniile care nu sunt produse.
Imaginile unui bon lung pot avea o zonă suprapusă; nu dubla aceeași linie aflată imediat înainte
și după marcajul «URMĂTOAREA IMAGINE».

TEXT FACTURĂ:
{text[:24000]}"""
    response = requests.post(
        f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
        json={
            "model": settings.OLLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "format": PRODUCT_SCHEMA,
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=180,
    )
    response.raise_for_status()
    content = response.json()["message"]["content"]
    return json.loads(content).get("products", [])


def _size_from_name(name):
    match = re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*(kg|gr?|g|ml|l)\b", name)
    if not match:
        return Decimal("1"), BaseUnit.PIECE
    size = _decimal(match.group(1), "1")
    unit = match.group(2).lower()
    if unit in {"g", "gr"}:
        return size / 1000, BaseUnit.KILOGRAM
    if unit == "kg":
        return size, BaseUnit.KILOGRAM
    if unit == "ml":
        return size / 1000, BaseUnit.LITER
    return size, BaseUnit.LITER


LINE_PATTERNS = [
    re.compile(
        r"^\s*(?:\d+[.)]\s*)?(?P<name>.+?)\s*[-:]\s*(?P<qty>\d+(?:[.,]\d+)?)\s*"
        r"(?:buc(?:ati)?|bax(?:uri)?|kg|l)?\s*[xX]\s*(?P<price>\d+(?:[.,]\d+)?)\s*(?:RON|LEI)?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<name>[A-Za-zĂÂÎȘȚăâîșț][^\n]{2,}?)\s+(?P<qty>\d+(?:[.,]\d+)?)\s*[xX]\s*"
        r"(?P<price>\d+(?:[.,]\d+)?)\s*(?:RON|LEI)?(?:\s+(?P<total>\d+(?:[.,]\d+)?))?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?P<name>.+?[A-Za-zĂÂÎȘȚăâîșț][^\n]*?)\s+"
        r"(?P<qty>\d+(?:[.,]\d{1,3})?)\s*(?P<qty_unit>BUC(?:ATA|ATI)?|KG|L)?\s+"
        r"(?:[xX*@]\s*)?(?P<price>\d+(?:[.,]\d{2}))\s+"
        r"(?P<total>\d+(?:[.,]\d{2}))(?:\s+[A-Z])?\s*$",
        re.IGNORECASE,
    ),
]


NON_PRODUCT_TERMS = {
    "total", "subtotal", "tva", "cui", "cif", "bon fiscal", "rest", "numerar", "card",
    "discount", "reducere", "operator", "casa", "ora", "data", "incasare", "plata",
}


def _is_non_product_name(name):
    lowered = " ".join(name.lower().split())
    return any(term == lowered or lowered.startswith(f"{term} ") for term in NON_PRODUCT_TERMS)


def parse_heuristic(text):
    products = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        for pattern in LINE_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            name = match.group("name").strip(" .-")
            if _is_non_product_name(name):
                break
            ean = ""
            code_match = re.match(r"^(\d{8,14})\s+(.+)$", name)
            if code_match:
                ean, name = code_match.groups()
            size, base_unit = _size_from_name(name)
            quantity_unit = (match.groupdict().get("qty_unit") or "").upper()
            if base_unit == BaseUnit.PIECE and quantity_unit in {BaseUnit.KILOGRAM, BaseUnit.LITER}:
                base_unit = quantity_unit
            quantity = _decimal(match.group("qty"), "1")
            price = _decimal(match.group("price"))
            products.append(
                {
                    "original_name": name,
                    "ean": ean,
                    "quantity": quantity,
                    "units_per_package": Decimal("1"),
                    "unit_size": size,
                    "base_unit": base_unit,
                    "unit_price_gross": price,
                    "vat_rate": Decimal("0"),
                    "line_total_gross": _decimal(match.groupdict().get("total")) if match.groupdict().get("total") else quantity * price,
                    "discount_gross": Decimal("0"),
                    "deposit_gross": Decimal("0"),
                }
            )
            break
    return products


def _candidate_line_count(text):
    count = 0
    for raw_line in text.splitlines():
        line = " ".join(raw_line.split())
        if len(line) < 5 or _is_non_product_name(line):
            continue
        decimal_values = re.findall(r"\b\d+[.,]\d{2}\b", line)
        has_sale_marker = bool(re.search(r"(?i)\b(?:buc(?:ata|ati)?|kg|l)\b|[xX*@]", line))
        if len(decimal_values) >= 2 or (decimal_values and has_sale_marker):
            count += 1
    return count


def _names_match(left, right):
    def normalize(value):
        return " ".join(re.findall(r"[a-z0-9]+", value.lower()))

    return fuzz.token_set_ratio(normalize(left), normalize(right)) >= 82


def _same_extracted_product(left, right):
    same_quantity = abs(left["quantity"] - right["quantity"]) <= Decimal("0.001")
    same_price = abs(left["unit_price_gross"] - right["unit_price_gross"]) <= Decimal("0.01")
    left_total = left.get("line_total_gross")
    right_total = right.get("line_total_gross")
    same_total = (
        left_total is not None
        and right_total is not None
        and abs(left_total - right_total) <= Decimal("0.02")
    )
    return _names_match(left["original_name"], right["original_name"]) and (
        (same_quantity and same_price) or same_total
    )


def _merge_products(deterministic, model_products):
    merged = list(deterministic)
    for product in model_products:
        if not any(_same_extracted_product(product, existing) for existing in merged):
            merged.append(product)
    return merged


def normalize_product_data(item):
    return {
        "original_name": str(item.get("original_name", "")).strip()[:240],
        "ean": str(item.get("ean", "")).strip()[:80],
        "quantity": max(_decimal(item.get("quantity"), "1"), Decimal("0.001")),
        "units_per_package": max(_decimal(item.get("units_per_package"), "1"), Decimal("0.001")),
        "unit_size": max(_decimal(item.get("unit_size"), "1"), Decimal("0.001")),
        "base_unit": item.get("base_unit") if item.get("base_unit") in BaseUnit.values else BaseUnit.PIECE,
        "unit_price_gross": max(_decimal(item.get("unit_price_gross")), Decimal("0")),
        "vat_rate": max(_decimal(item.get("vat_rate")), Decimal("0")),
        "line_total_gross": _decimal(item["line_total_gross"]) if item.get("line_total_gross") is not None else None,
        "discount_gross": max(_decimal(item.get("discount_gross")), Decimal("0")),
        "deposit_gross": max(_decimal(item.get("deposit_gross")), Decimal("0")),
    }


def _apply_default_vat(products, default_vat_rate):
    if default_vat_rate is None:
        return products
    rate = max(_decimal(default_vat_rate), Decimal("0"))
    for product in products:
        if product["vat_rate"] == 0:
            product["vat_rate"] = rate
    return products


def parse_invoice_text(text, parser_mode="AUTO", default_vat_rate=None):
    # Formatele explicite de tip „10 bucăți x 7,90” sunt mai sigure și mult mai
    # rapide cu parserul determinist. Ollama intervine pentru layout-uri OCR mai
    # dezordonate, nu pentru a reinterpreta date deja clare.
    heuristic_products = [normalize_product_data(item) for item in parse_heuristic(text)]
    candidate_count = _candidate_line_count(text)
    needs_model = (
        parser_mode == "OLLAMA"
        or (parser_mode == "AUTO" and (not heuristic_products or candidate_count > len(heuristic_products)))
    )
    if parser_mode == "HEURISTIC" or (heuristic_products and (not settings.OLLAMA_ENABLED or not needs_model)):
        warning = "Ollama local este dezactivat." if parser_mode == "OLLAMA" and not settings.OLLAMA_ENABLED else None
        return _apply_default_vat(heuristic_products, default_vat_rate), "heuristic", warning

    ollama_error = (
        "Ollama local este dezactivat."
        if parser_mode == "OLLAMA" and not settings.OLLAMA_ENABLED
        else None
    )
    if settings.OLLAMA_ENABLED:
        try:
            parsed = parse_with_ollama(text)
            model_products = [normalize_product_data(item) for item in parsed if item.get("original_name")]
            products = _merge_products(heuristic_products, model_products)
            if products:
                return (
                    _apply_default_vat(products, default_vat_rate),
                    "hybrid" if heuristic_products and model_products else "ollama",
                    None,
                )
        except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as exc:
            ollama_error = str(exc)

    return _apply_default_vat(heuristic_products, default_vat_rate), "heuristic", ollama_error

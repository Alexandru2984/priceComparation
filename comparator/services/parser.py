import json
import re
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings

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
                    "quantity": {"type": "number"},
                    "units_per_package": {"type": "number"},
                    "unit_size": {"type": "number"},
                    "base_unit": {"type": "string", "enum": ["BUC", "KG", "L"]},
                    "unit_price_gross": {"type": "number"},
                    "vat_rate": {"type": "number"},
                    "line_total_gross": {"type": ["number", "null"]},
                },
                "required": [
                    "original_name", "quantity", "units_per_package", "unit_size", "base_unit",
                    "unit_price_gross", "vat_rate", "line_total_gross",
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
]


def parse_heuristic(text):
    products = []
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        for pattern in LINE_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            name = match.group("name").strip(" .-")
            if name.lower() in {"total", "subtotal", "tva"} or any(
                word in name.lower() for word in ("discount", "reducere", "rest plata")
            ):
                break
            size, base_unit = _size_from_name(name)
            quantity = _decimal(match.group("qty"), "1")
            price = _decimal(match.group("price"))
            products.append(
                {
                    "original_name": name,
                    "quantity": quantity,
                    "units_per_package": Decimal("1"),
                    "unit_size": size,
                    "base_unit": base_unit,
                    "unit_price_gross": price,
                    "vat_rate": Decimal("0"),
                    "line_total_gross": _decimal(match.groupdict().get("total")) if match.groupdict().get("total") else quantity * price,
                }
            )
            break
    return products


def normalize_product_data(item):
    return {
        "original_name": str(item.get("original_name", "")).strip()[:240],
        "quantity": max(_decimal(item.get("quantity"), "1"), Decimal("0.001")),
        "units_per_package": max(_decimal(item.get("units_per_package"), "1"), Decimal("0.001")),
        "unit_size": max(_decimal(item.get("unit_size"), "1"), Decimal("0.001")),
        "base_unit": item.get("base_unit") if item.get("base_unit") in BaseUnit.values else BaseUnit.PIECE,
        "unit_price_gross": max(_decimal(item.get("unit_price_gross")), Decimal("0")),
        "vat_rate": max(_decimal(item.get("vat_rate")), Decimal("0")),
        "line_total_gross": _decimal(item["line_total_gross"]) if item.get("line_total_gross") is not None else None,
    }


def parse_invoice_text(text):
    # Formatele explicite de tip „10 bucăți x 7,90” sunt mai sigure și mult mai
    # rapide cu parserul determinist. Ollama intervine pentru layout-uri OCR mai
    # dezordonate, nu pentru a reinterpreta date deja clare.
    heuristic_products = [normalize_product_data(item) for item in parse_heuristic(text)]
    if heuristic_products:
        return heuristic_products, "heuristic", None

    ollama_error = None
    if settings.OLLAMA_ENABLED:
        try:
            parsed = parse_with_ollama(text)
            products = [normalize_product_data(item) for item in parsed if item.get("original_name")]
            if products:
                return products, "ollama", None
        except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as exc:
            ollama_error = str(exc)

    return [], "heuristic", ollama_error

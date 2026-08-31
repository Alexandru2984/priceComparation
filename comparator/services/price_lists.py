import io
import re
import unicodedata
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from django.db import transaction
from django.utils import timezone

from comparator.models import BaseUnit, Invoice, InvoiceLine, SupplierPriceImport

from .matching import apply_match, suggest_product

MAX_ROWS = 5000
MAX_XLSX_UNCOMPRESSED = 50 * 1024 * 1024

COLUMN_ALIASES = {
    "name": {"produs", "denumire", "nume", "product", "description", "descriere", "articol"},
    "price": {"pret", "pret cu tva", "pret unitar", "price", "unit price", "pret vanzare"},
    "ean": {"ean", "gtin", "cod bare", "cod de bare", "barcode", "cod produs", "sku"},
    "size": {"gramaj", "cantitate", "unit size", "marime", "volum"},
    "unit": {"unitate", "um", "u m", "unit", "unitate masura"},
    "pack": {"bucati bax", "buc bax", "bucati per bax", "units per package", "ambalare", "multiplu"},
}


def _normalize(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def _column_mapping(columns):
    normalized = {_normalize(column): column for column in columns}
    mapping = {}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[target] = normalized[alias]
                break
        if target not in mapping:
            for normalized_name, original in normalized.items():
                if any(alias in normalized_name for alias in aliases):
                    mapping[target] = original
                    break
    return mapping


def _decimal(value, default=None):
    if value is None or pd.isna(value):
        return default
    cleaned = re.sub(r"[^0-9,.-]", "", str(value)).replace(".", "").replace(",", ".")
    # A plain decimal point is not a thousands separator.
    original = str(value)
    if "," not in original and original.count(".") == 1:
        cleaned = re.sub(r"[^0-9.-]", "", original)
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return default


def _size_and_unit(size_value, unit_value, name):
    combined = " ".join(str(value) for value in (size_value, unit_value) if value is not None and not pd.isna(value))
    match = re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*(kg|gr?|g|ml|l|buc)?\b", combined)
    if not match:
        match = re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*(kg|gr?|g|ml|l)\b", name)
    if not match:
        hint = _normalize(unit_value or "")
        return Decimal("1"), BaseUnit.KILOGRAM if hint == "kg" else BaseUnit.LITER if hint == "l" else BaseUnit.PIECE
    size = Decimal(match.group(1).replace(",", "."))
    unit = (match.group(2) or _normalize(unit_value or "")).lower()
    if unit in {"g", "gr"}:
        return size / 1000, BaseUnit.KILOGRAM
    if unit == "kg":
        return size, BaseUnit.KILOGRAM
    if unit == "ml":
        return size / 1000, BaseUnit.LITER
    if unit == "l":
        return size, BaseUnit.LITER
    return Decimal("1"), BaseUnit.PIECE


def _read_table(upload):
    extension = Path(upload.name).suffix.lower()
    data = upload.read()
    if extension == ".xlsx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_XLSX_UNCOMPRESSED:
                raise ValueError("Fișierul XLSX se extinde la peste 50 MB și a fost refuzat.")
        return pd.read_excel(io.BytesIO(data), dtype=str).head(MAX_ROWS + 1)
    for encoding in ("utf-8-sig", "cp1250", "latin1"):
        try:
            return pd.read_csv(
                io.BytesIO(data),
                dtype=str,
                sep=None,
                engine="python",
                encoding=encoding,
                nrows=MAX_ROWS + 1,
            )
        except UnicodeDecodeError:
            continue
    raise ValueError("Codarea fișierului CSV nu a putut fi recunoscută.")


def parse_supplier_price_list(upload, supplier):
    frame = _read_table(upload)
    if len(frame) > MAX_ROWS:
        raise ValueError(f"Lista poate avea maximum {MAX_ROWS} rânduri.")
    mapping = _column_mapping(frame.columns)
    if "name" not in mapping or "price" not in mapping:
        columns = ", ".join(str(column) for column in frame.columns[:20])
        raise ValueError(f"Nu am găsit coloanele produs/denumire și preț. Coloane detectate: {columns}")

    rows = []
    for index, record in frame.iterrows():
        name = str(record.get(mapping["name"], "")).strip()
        price = _decimal(record.get(mapping["price"]))
        errors = []
        if not name or name.lower() == "nan":
            errors.append("Denumire lipsă")
        if price is None or price <= 0:
            errors.append("Preț invalid")
        raw_ean = str(record.get(mapping.get("ean"), "") or "").strip()
        ean = re.sub(r"\D", "", raw_ean.removesuffix(".0"))[:80]
        unit_size, base_unit = _size_and_unit(
            record.get(mapping.get("size")),
            record.get(mapping.get("unit")),
            name,
        )
        units_per_package = _decimal(record.get(mapping.get("pack")), Decimal("1")) or Decimal("1")
        product, score = (None, 0)
        if name and not errors:
            product, score = suggest_product(
                name,
                supplier=supplier,
                base_unit=base_unit,
                code=ean,
                unit_size=unit_size,
                units_per_package=units_per_package,
            )
        rows.append({
            "row": int(index) + 2,
            "name": name[:240],
            "ean": ean,
            "price": str(price) if price is not None else "",
            "units_per_package": str(max(units_per_package, Decimal("0.001"))),
            "unit_size": str(unit_size),
            "base_unit": base_unit,
            "product_id": product.pk if product else None,
            "product_name": product.name if product else "",
            "match_score": score,
            "errors": errors,
        })
    return rows


@transaction.atomic
def create_price_list_invoice(price_import):
    price_import = SupplierPriceImport.objects.select_for_update().select_related("supplier").get(pk=price_import.pk)
    if price_import.imported_invoice_id:
        return price_import.imported_invoice, False
    valid_rows = [row for row in price_import.rows if not row.get("errors")]
    if not valid_rows:
        raise ValueError("Lista nu conține niciun rând valid pentru import.")
    invoice = Invoice.objects.create(
        supplier=price_import.supplier,
        document_type=Invoice.DocumentType.PRICE_LIST,
        number=f"LISTA-{price_import.pk}",
        issued_at=price_import.effective_at,
        status=Invoice.Status.REVIEW,
        notes=f"Import din {price_import.original_filename}. Confirmă potrivirile înainte de folosirea prețurilor.",
    )
    for row in valid_rows:
        line = InvoiceLine(
            invoice=invoice,
            original_name=row["name"],
            ean=row.get("ean", ""),
            quantity=Decimal("1"),
            units_per_package=Decimal(row["units_per_package"]),
            unit_size=Decimal(row["unit_size"]),
            base_unit=row["base_unit"],
            unit_price_gross=Decimal(row["price"]),
            line_total_gross=Decimal(row["price"]),
            needs_review=True,
        )
        apply_match(line)
        line.needs_review = True
        line.save()
    price_import.status = SupplierPriceImport.Status.IMPORTED
    price_import.imported_invoice = invoice
    price_import.imported_at = timezone.now()
    price_import.save(update_fields=["status", "imported_invoice", "imported_at"])
    return invoice, True

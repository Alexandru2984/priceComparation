import hashlib
import re
from datetime import datetime, time
from decimal import Decimal

import pandas as pd
from django.db import IntegrityError, transaction
from django.utils import timezone

from comparator.models import InventoryItem, SalesImport, SalesImportLine, StockMovement

from .matching import suggest_product
from .price_lists import _decimal, _normalize, _read_table


SALES_COLUMN_ALIASES = {
    "name": {"produs", "denumire", "nume", "product", "articol"},
    "ean": {"ean", "gtin", "cod bare", "cod de bare", "barcode", "cod produs", "plu"},
    "quantity": {"cantitate", "qty", "quantity", "bucati", "buc", "volum vandut"},
    "sold_at": {"data", "data vanzare", "sold at", "datetime", "ora vanzare"},
    "reference": {"bon", "numar bon", "referinta", "receipt", "transaction id", "id tranzactie"},
}


def _mapping(columns):
    normalized = {_normalize(column): column for column in columns}
    result = {}
    for target, aliases in SALES_COLUMN_ALIASES.items():
        for name, original in normalized.items():
            if name in aliases or any(alias in name for alias in aliases):
                result[target] = original
                break
    return result


def _sold_at(value, default_date):
    if value is None or pd.isna(value) or not str(value).strip():
        parsed = datetime.combine(default_date, time(12, 0))
    else:
        try:
            parsed = pd.to_datetime(value, dayfirst=True).to_pydatetime()
        except (ValueError, TypeError, OverflowError):
            return None
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def parse_sales_file(upload, default_date):
    raw = upload.read()
    file_hash = hashlib.sha256(raw).hexdigest()
    upload.seek(0)
    frame = _read_table(upload)
    mapping = _mapping(frame.columns)
    if "quantity" not in mapping or ("name" not in mapping and "ean" not in mapping):
        columns = ", ".join(str(column) for column in frame.columns[:20])
        raise ValueError(f"Sunt necesare cantitate și produs sau EAN. Coloane detectate: {columns}")
    rows = []
    for index, record in frame.iterrows():
        row_number = int(index) + 2
        name = str(record.get(mapping.get("name"), "") or "").strip()
        if name.lower() == "nan":
            name = ""
        raw_ean = str(record.get(mapping.get("ean"), "") or "").strip()
        ean = re.sub(r"\D", "", raw_ean.removesuffix(".0"))[:80]
        quantity = _decimal(record.get(mapping["quantity"]))
        sold_at = _sold_at(record.get(mapping.get("sold_at")), default_date)
        reference = str(record.get(mapping.get("reference"), "") or "").strip()
        if reference.lower() == "nan":
            reference = ""
        errors = []
        if not name and not ean:
            errors.append("Produs/EAN lipsă")
        if quantity is None or quantity <= 0:
            errors.append("Cantitate invalidă")
        if sold_at is None:
            errors.append("Dată invalidă")
        product, score = (None, 0)
        if not errors:
            product, score = suggest_product(name or ean, code=ean)
        identity = (
            f"ref:{_normalize(reference)}|{ean or _normalize(name)}|{quantity}"
            if reference
            else f"file:{file_hash}|row:{row_number}"
        )
        rows.append({
            "row_number": row_number,
            "source_key": hashlib.sha256(identity.encode()).hexdigest(),
            "external_reference": reference[:120],
            "sold_at": sold_at,
            "original_name": name[:240],
            "ean": ean,
            "quantity": quantity or Decimal("0"),
            "product": product,
            "match_score": score,
            "error": "; ".join(errors),
        })
    return file_hash, rows


@transaction.atomic
def apply_sales_import(sales_import):
    sales_import = SalesImport.objects.select_for_update().get(pk=sales_import.pk)
    applied = 0
    duplicates = 0
    pending = 0
    now = timezone.now()
    lines = sales_import.lines.select_for_update().order_by("row_number")
    for line in lines:
        if line.ignored or line.applied_at:
            continue
        existing = StockMovement.objects.filter(source_key=line.source_key).first()
        if existing:
            line.applied_at = existing.created_at
            line.save(update_fields=["applied_at"])
            duplicates += 1
            continue
        if line.error or not line.product_id or line.match_score < 75:
            pending += 1
            continue
        inventory = InventoryItem.objects.filter(product=line.product, active=True).first()
        if not inventory:
            line.error = "Produsul nu are o politică de stoc activă. Configureaz-o înainte de aplicare."
            line.save(update_fields=["error"])
            pending += 1
            continue
        base_quantity = line.quantity * inventory.retail_unit_size
        try:
            with transaction.atomic():
                StockMovement.objects.create(
                    inventory_item=inventory,
                    quantity_delta=-base_quantity,
                    reason=StockMovement.Reason.SALE,
                    sale_line=line,
                    source_key=line.source_key,
                    note=f"Vânzare POS {line.external_reference or sales_import.original_filename}"[:240],
                    created_by=sales_import.created_by,
                )
        except IntegrityError:
            # Un alt import/worker a aplicat aceeași vânzare între verificare și insert.
            if StockMovement.objects.filter(source_key=line.source_key).exists():
                line.applied_at = now
                line.save(update_fields=["applied_at"])
                duplicates += 1
                continue
            raise
        line.applied_at = now
        line.error = ""
        line.save(update_fields=["applied_at", "error"])
        applied += 1
    sales_import.status = SalesImport.Status.PARTIAL if pending else SalesImport.Status.APPLIED
    sales_import.applied_at = now
    sales_import.warning_count = pending
    sales_import.save(update_fields=["status", "applied_at", "warning_count"])
    return {"applied": applied, "duplicates": duplicates, "pending": pending}

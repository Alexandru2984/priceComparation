import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from rapidfuzz import fuzz

from .ocr import extract_text_result
from .parser import parse_invoice_text


def _decimal(value):
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _close(left, right, tolerance):
    left_value = _decimal(left)
    right_value = _decimal(right)
    return left_value is not None and right_value is not None and abs(left_value - right_value) <= tolerance


def _name_score(left, right):
    return fuzz.token_set_ratio(str(left).lower(), str(right).lower())


def evaluate_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifestul trebuie să conțină o listă nevidă «cases».")
    totals = {
        "expected": 0, "predicted": 0, "matched": 0,
        "quantity_correct": 0, "price_correct": 0, "unit_correct": 0,
        "ean_checked": 0, "ean_correct": 0,
    }
    results = []
    for case_index, case in enumerate(cases, start=1):
        name = str(case.get("name") or f"case-{case_index}")
        ocr_metadata = None
        if case.get("file"):
            source_path = (manifest_path.parent / str(case["file"])).resolve()
            if manifest_path.parent not in source_path.parents and source_path != manifest_path.parent:
                raise ValueError(f"{name}: calea fișierului iese din directorul manifestului.")
            ocr_result = extract_text_result(source_path)
            text = ocr_result.text
            ocr_metadata = {
                "quality_score": ocr_result.quality_score,
                "strategy": ocr_result.strategy,
                "warnings": ocr_result.warnings,
            }
        else:
            text = str(case.get("ocr_text", ""))
        expected = case.get("expected", [])
        if not isinstance(expected, list):
            raise ValueError(f"{name}: «expected» trebuie să fie listă.")
        predicted, parser_name, warning = parse_invoice_text(
            text,
            parser_mode=str(case.get("parser_mode", "HEURISTIC")).upper(),
        )
        totals["expected"] += len(expected)
        totals["predicted"] += len(predicted)
        unused = set(range(len(predicted)))
        matches = []
        for expected_item in expected:
            ranked = sorted(
                ((index, _name_score(expected_item.get("original_name", ""), predicted[index]["original_name"])) for index in unused),
                key=lambda item: item[1],
                reverse=True,
            )
            if not ranked or ranked[0][1] < 80:
                matches.append({"expected": expected_item, "predicted": None, "name_score": ranked[0][1] if ranked else 0})
                continue
            index, score = ranked[0]
            unused.remove(index)
            actual = predicted[index]
            totals["matched"] += 1
            quantity_correct = _close(expected_item.get("quantity"), actual.get("quantity"), Decimal("0.001"))
            price_correct = _close(expected_item.get("unit_price_gross"), actual.get("unit_price_gross"), Decimal("0.01"))
            unit_correct = expected_item.get("base_unit") == actual.get("base_unit")
            totals["quantity_correct"] += quantity_correct
            totals["price_correct"] += price_correct
            totals["unit_correct"] += unit_correct
            ean_correct = None
            if "ean" in expected_item:
                totals["ean_checked"] += 1
                ean_correct = str(expected_item.get("ean", "")) == str(actual.get("ean", ""))
                totals["ean_correct"] += ean_correct
            matches.append({
                "expected": expected_item,
                "predicted": {key: str(value) for key, value in actual.items()},
                "name_score": round(score, 1),
                "quantity_correct": quantity_correct,
                "price_correct": price_correct,
                "unit_correct": unit_correct,
                "ean_correct": ean_correct,
            })
        results.append({
            "name": name,
            "parser": parser_name,
            "warning": warning,
            "ocr": ocr_metadata,
            "expected_count": len(expected),
            "predicted_count": len(predicted),
            "unmatched_predictions": [predicted[index]["original_name"] for index in sorted(unused)],
            "matches": matches,
        })
    matched = totals["matched"]
    metrics = {
        "precision": round(matched * 100 / totals["predicted"], 2) if totals["predicted"] else 0,
        "recall": round(matched * 100 / totals["expected"], 2) if totals["expected"] else 0,
        "quantity_accuracy": round(totals["quantity_correct"] * 100 / matched, 2) if matched else 0,
        "price_accuracy": round(totals["price_correct"] * 100 / matched, 2) if matched else 0,
        "unit_accuracy": round(totals["unit_correct"] * 100 / matched, 2) if matched else 0,
        "ean_accuracy": round(totals["ean_correct"] * 100 / totals["ean_checked"], 2) if totals["ean_checked"] else None,
    }
    return {"manifest": str(manifest_path), "totals": totals, "metrics": metrics, "cases": results}

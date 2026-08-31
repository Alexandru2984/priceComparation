import json
from collections import Counter
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


def empty_totals():
    return {
        "expected": 0,
        "predicted": 0,
        "matched": 0,
        "quantity_correct": 0,
        "price_correct": 0,
        "unit_correct": 0,
        "ean_checked": 0,
        "ean_correct": 0,
    }


def metrics_from_totals(totals):
    matched = totals["matched"]
    return {
        "precision": round(matched * 100 / totals["predicted"], 2) if totals["predicted"] else 0,
        "recall": round(matched * 100 / totals["expected"], 2) if totals["expected"] else 0,
        "quantity_accuracy": round(totals["quantity_correct"] * 100 / matched, 2) if matched else 0,
        "price_accuracy": round(totals["price_correct"] * 100 / matched, 2) if matched else 0,
        "unit_accuracy": round(totals["unit_correct"] * 100 / matched, 2) if matched else 0,
        "ean_accuracy": round(totals["ean_correct"] * 100 / totals["ean_checked"], 2)
        if totals["ean_checked"]
        else None,
    }


def evaluate_line_items(expected, predicted):
    totals = empty_totals()
    totals["expected"] = len(expected)
    totals["predicted"] = len(predicted)
    unused = set(range(len(predicted)))
    matches = []
    for expected_item in expected:
        ranked = sorted(
            (
                (index, _name_score(expected_item.get("original_name", ""), predicted[index]["original_name"]))
                for index in unused
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        if not ranked or ranked[0][1] < 80:
            matches.append(
                {
                    "expected": expected_item,
                    "predicted": None,
                    "name_score": ranked[0][1] if ranked else 0,
                }
            )
            continue
        index, score = ranked[0]
        unused.remove(index)
        actual = predicted[index]
        totals["matched"] += 1
        quantity_correct = _close(expected_item.get("quantity"), actual.get("quantity"), Decimal("0.001"))
        price_correct = _close(
            expected_item.get("unit_price_gross"), actual.get("unit_price_gross"), Decimal("0.01")
        )
        unit_correct = expected_item.get("base_unit") == actual.get("base_unit")
        totals["quantity_correct"] += quantity_correct
        totals["price_correct"] += price_correct
        totals["unit_correct"] += unit_correct
        ean_correct = None
        if "ean" in expected_item:
            totals["ean_checked"] += 1
            ean_correct = str(expected_item.get("ean", "")) == str(actual.get("ean", ""))
            totals["ean_correct"] += ean_correct
        matches.append(
            {
                "expected": expected_item,
                "predicted": {key: str(value) for key, value in actual.items()},
                "name_score": round(score, 1),
                "quantity_correct": quantity_correct,
                "price_correct": price_correct,
                "unit_correct": unit_correct,
                "ean_correct": ean_correct,
            }
        )
    return {
        "totals": totals,
        "matches": matches,
        "unmatched_predictions": [predicted[index]["original_name"] for index in sorted(unused)],
    }


def evaluate_manifest(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Manifestul trebuie să conțină o listă nevidă «cases».")
    totals = empty_totals()
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
        comparison = evaluate_line_items(expected, predicted)
        for key, value in comparison["totals"].items():
            totals[key] += value
        results.append({
            "name": name,
            "parser": parser_name,
            "warning": warning,
            "ocr": ocr_metadata,
            "expected_count": len(expected),
            "predicted_count": len(predicted),
            "unmatched_predictions": comparison["unmatched_predictions"],
            "matches": comparison["matches"],
        })
    return {
        "manifest": str(manifest_path),
        "totals": totals,
        "metrics": metrics_from_totals(totals),
        "cases": results,
    }


def evaluate_confirmed_documents(invoices):
    totals = empty_totals()
    rows = []
    recurring = Counter()
    evaluated_count = 0
    quality_scores = []

    for invoice in invoices:
        lines = list(invoice.lines.all())
        pages = list(invoice.pages.all())
        reasons = []
        if not invoice.ocr_text.strip():
            reasons.append("lipsește textul OCR")
        if not lines:
            reasons.append("nu are linii confirmate")
        if any(line.needs_review for line in lines):
            reasons.append("mai are linii de verificat")

        page_scores = [page.ocr_quality_score for page in pages if page.ocr_text]
        quality_scores.extend(page_scores)
        for page in pages:
            for warning in page.ocr_warnings:
                recurring[f"Imagine: {warning}"] += 1
        average_quality = round(sum(page_scores) / len(page_scores), 1) if page_scores else None

        row = {
            "invoice": invoice,
            "ready": not reasons,
            "reason": "; ".join(reasons),
            "ocr_quality": average_quality,
            "expected_count": len(lines),
        }
        if reasons:
            rows.append(row)
            continue

        expected = []
        for line in lines:
            item = {
                "original_name": line.original_name,
                "quantity": line.quantity,
                "unit_price_gross": line.unit_price_gross,
                "base_unit": line.base_unit,
            }
            if line.ean:
                item["ean"] = line.ean
            expected.append(item)

        predicted, parser_name, parser_warning = parse_invoice_text(
            invoice.ocr_text,
            parser_mode="HEURISTIC",
        )
        comparison = evaluate_line_items(expected, predicted)
        for key, value in comparison["totals"].items():
            totals[key] += value
        evaluated_count += 1

        for match in comparison["matches"]:
            if match["predicted"] is None:
                recurring["Produs lipsă din extragere"] += 1
                continue
            if not match["quantity_correct"]:
                recurring["Cantitate diferită"] += 1
            if not match["price_correct"]:
                recurring["Preț diferit"] += 1
            if not match["unit_correct"]:
                recurring["Unitate diferită"] += 1
            if match["ean_correct"] is False:
                recurring["EAN diferit"] += 1
        if comparison["unmatched_predictions"]:
            recurring["Linii suplimentare extrase"] += len(comparison["unmatched_predictions"])
        if parser_warning:
            recurring[f"Parser: {parser_warning}"] += 1
        if invoice.document_total_gross is not None and not invoice.is_reconciled:
            recurring["Total document nereconciliat"] += 1

        row.update(
            {
                "parser": parser_name,
                "parser_warning": parser_warning,
                "metrics": metrics_from_totals(comparison["totals"]),
                "predicted_count": comparison["totals"]["predicted"],
            }
        )
        rows.append(row)

    return {
        "selected_count": len(rows),
        "evaluated_count": evaluated_count,
        "pending_count": len(rows) - evaluated_count,
        "totals": totals,
        "metrics": metrics_from_totals(totals),
        "average_ocr_quality": round(sum(quality_scores) / len(quality_scores), 1) if quality_scores else None,
        "low_quality_pages": sum(score < 60 for score in quality_scores),
        "recurring_issues": recurring.most_common(10),
        "rows": rows,
    }

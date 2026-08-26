from dataclasses import dataclass
from pathlib import Path
import re

from django.conf import settings
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class OCRUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRResult:
    text: str
    quality_score: int
    strategy: str
    warnings: list[str]


def _prepare_image(image):
    image = ImageOps.exif_transpose(image).convert("L")
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Contrast(image).enhance(1.4)
    if image.width < 1800:
        ratio = 1800 / image.width
        image = image.resize((1800, int(image.height * ratio)))
    return image.filter(ImageFilter.SHARPEN)


def _ocr_quality(text):
    useful = sum(character.isalnum() for character in text or "")
    lines = sum(1 for line in (text or "").splitlines() if len(line.strip()) >= 4)
    return useful + lines * 12


def _quality_score(text):
    text = text or ""
    if not text.strip():
        return 0
    useful = sum(character.isalnum() for character in text)
    visible = sum(not character.isspace() for character in text)
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 4]
    price_lines = sum(bool(re.search(r"\d+[,.]\d{2}", line)) for line in lines)
    useful_ratio = useful / max(visible, 1)
    # Un bon poate avea doar câteva rânduri, dar acestea pot fi perfect clare;
    # lungimea nu trebuie să penalizeze documentele scurte.
    score = min(55, int(useful * 1.15)) + min(25, len(lines) * 3) + min(15, price_lines * 5)
    if useful_ratio >= 0.82:
        score += 5
    elif useful_ratio < 0.55:
        score -= 15
    return max(0, min(100, int(score)))


def _image_warnings(image, score):
    warnings = []
    if image.width < 900:
        warnings.append("Rezoluție mică; fotografiază documentul mai aproape.")
    if image.height and max(image.width / image.height, image.height / image.width) > 5:
        warnings.append("Imagine foarte îngustă; verifică dacă bonul este surprins complet.")
    if score < 45:
        warnings.append("Textul extras are încredere redusă și necesită verificare atentă.")
    return warnings


def _tesseract_image(image):
    try:
        import pytesseract

        prepared = _prepare_image(image)
        candidates = []
        primary = pytesseract.image_to_string(
            prepared, lang=settings.OCR_LANGUAGE, config="--oem 3 --psm 6 -c preserve_interword_spaces=1"
        )
        candidates.append(("PSM6", primary))
        if _quality_score(primary) < 70:
            threshold = prepared.point(lambda pixel: 255 if pixel > 165 else 0)
            candidates.append((
                "PSM4",
                pytesseract.image_to_string(
                    threshold,
                    lang=settings.OCR_LANGUAGE,
                    config="--oem 3 --psm 4 -c preserve_interword_spaces=1",
                ),
            ))
        if max(_quality_score(text) for _, text in candidates) < 55:
            candidates.append((
                "PSM11",
                pytesseract.image_to_string(
                    prepared,
                    lang=settings.OCR_LANGUAGE,
                    config="--oem 3 --psm 11 -c preserve_interword_spaces=1",
                ),
            ))
        strategy, text = max(candidates, key=lambda item: (_quality_score(item[1]), _ocr_quality(item[1])))
        score = _quality_score(text)
        warnings = _image_warnings(image, score)
        if len(candidates) > 1:
            warnings.append(f"OCR a încercat automat {len(candidates)} moduri și a ales {strategy}.")
        return OCRResult(text=text, quality_score=score, strategy=strategy, warnings=warnings)
    except pytesseract.pytesseract.TesseractNotFoundError as exc:
        raise OCRUnavailable("Tesseract nu este instalat sau nu se află în PATH.") from exc
    except pytesseract.pytesseract.TesseractError:
        try:
            text = pytesseract.image_to_string(_prepare_image(image), lang="eng", config="--oem 3 --psm 6")
            score = _quality_score(text)
            return OCRResult(
                text=text,
                quality_score=score,
                strategy="PSM6_ENG",
                warnings=_image_warnings(image, score) + ["Limba OCR configurată a eșuat; s-a folosit engleză."],
            )
        except Exception as exc:
            raise OCRUnavailable(f"Tesseract nu a putut procesa documentul: {exc}") from exc


def _native_pdf_text(page):
    text_page = None
    try:
        text_page = page.get_textpage()
        text = text_page.get_text_range() or ""
        return text if _ocr_quality(text) >= 120 else ""
    except Exception:
        return ""
    finally:
        if text_page is not None:
            text_page.close()


def extract_text_result(file_path):
    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(str(path))
            results = []
            for page_number, page in enumerate(document, start=1):
                native_text = _native_pdf_text(page)
                if native_text:
                    results.append(OCRResult(native_text, _quality_score(native_text), "NATIVE_PDF", []))
                else:
                    image = page.render(scale=2.2).to_pil()
                    result = _tesseract_image(image)
                    results.append(OCRResult(
                        result.text,
                        result.quality_score,
                        result.strategy,
                        [f"Pagina PDF {page_number}: {warning}" for warning in result.warnings],
                    ))
            if not results:
                return OCRResult("", 0, "PDF_EMPTY", ["PDF-ul nu conține pagini lizibile."])
            strategies = {result.strategy for result in results}
            return OCRResult(
                text="\n\n".join(result.text for result in results),
                quality_score=round(sum(result.quality_score for result in results) / len(results)),
                strategy=next(iter(strategies)) if len(strategies) == 1 else "MIXED_PDF",
                warnings=[warning for result in results for warning in result.warnings],
            )
        except OCRUnavailable:
            raise
        except Exception as exc:
            raise OCRUnavailable(f"PDF-ul nu a putut fi citit: {exc}") from exc

    try:
        with Image.open(path) as image:
            return _tesseract_image(image)
    except OCRUnavailable:
        raise
    except Exception as exc:
        raise OCRUnavailable(f"Imaginea nu a putut fi citită: {exc}") from exc


def extract_text(file_path):
    """Compatibility wrapper for callers that only need the extracted text."""
    return extract_text_result(file_path).text


def merge_ocr_pages(chunks):
    """Join receipt photos while removing exact OCR line overlap between adjacent images."""
    merged = []
    previous_normalized = []
    for chunk in chunks:
        lines = [line.rstrip() for line in (chunk or "").splitlines() if line.strip()]
        normalized = [" ".join(line.lower().split()) for line in lines]
        overlap = 0
        for size in range(min(8, len(previous_normalized), len(normalized)), 0, -1):
            if previous_normalized[-size:] == normalized[:size]:
                overlap = size
                break
        if merged:
            merged.append("--- URMĂTOAREA IMAGINE ---")
        merged.extend(lines[overlap:])
        previous_normalized = normalized
    return "\n\n".join(merged)

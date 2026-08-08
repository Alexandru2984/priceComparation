from pathlib import Path

from django.conf import settings
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class OCRUnavailable(RuntimeError):
    pass


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


def _tesseract_image(image):
    try:
        import pytesseract

        prepared = _prepare_image(image)
        primary = pytesseract.image_to_string(
            prepared, lang=settings.OCR_LANGUAGE, config="--oem 3 --psm 6 -c preserve_interword_spaces=1"
        )
        if _ocr_quality(primary) >= 180:
            return primary
        threshold = prepared.point(lambda pixel: 255 if pixel > 165 else 0)
        alternative = pytesseract.image_to_string(
            threshold, lang=settings.OCR_LANGUAGE, config="--oem 3 --psm 4 -c preserve_interword_spaces=1"
        )
        return alternative if _ocr_quality(alternative) > _ocr_quality(primary) else primary
    except pytesseract.pytesseract.TesseractNotFoundError as exc:
        raise OCRUnavailable("Tesseract nu este instalat sau nu se află în PATH.") from exc
    except pytesseract.pytesseract.TesseractError:
        try:
            return pytesseract.image_to_string(_prepare_image(image), lang="eng", config="--oem 3 --psm 6")
        except Exception as exc:
            raise OCRUnavailable(f"Tesseract nu a putut procesa documentul: {exc}") from exc


def extract_text(file_path):
    path = Path(file_path)
    if path.suffix.lower() == ".pdf":
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(str(path))
            chunks = []
            for page in document:
                image = page.render(scale=2.2).to_pil()
                chunks.append(_tesseract_image(image))
            return "\n\n".join(chunks)
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

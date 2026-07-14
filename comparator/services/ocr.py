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


def _tesseract_image(image):
    try:
        import pytesseract

        return pytesseract.image_to_string(
            _prepare_image(image), lang=settings.OCR_LANGUAGE, config="--oem 3 --psm 6"
        )
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


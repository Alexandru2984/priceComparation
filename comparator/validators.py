from pathlib import Path

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


MAX_DOCUMENT_SIZE = 10 * 1024 * 1024
MAX_DOCUMENT_TOTAL_SIZE = 50 * 1024 * 1024
MAX_CSV_SIZE = 2 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF"}
ALLOWED_DOCUMENT_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".pdf"}


def validate_document_upload(upload):
    extension = Path(upload.name).suffix.lower()
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS:
        raise ValidationError("Sunt acceptate numai PDF, JPG, PNG, WEBP și TIFF.")
    if not upload.size or upload.size > MAX_DOCUMENT_SIZE:
        raise ValidationError("Fiecare fișier trebuie să aibă maximum 10 MB.")

    position = upload.tell()
    try:
        if extension == ".pdf":
            pdf_data = upload.read()
            if not pdf_data.startswith(b"%PDF-"):
                raise ValidationError("Fișierul încărcat nu este un PDF valid.")
            try:
                import pypdfium2 as pdfium

                document = pdfium.PdfDocument(pdf_data)
                page_count = len(document)
                document.close()
            except Exception as exc:
                raise ValidationError("Fișierul încărcat nu este un PDF valid.") from exc
            if not page_count or page_count > 25:
                raise ValidationError("Un PDF poate avea între 1 și 25 de pagini.")
        else:
            try:
                with Image.open(upload) as image:
                    if image.format not in ALLOWED_IMAGE_FORMATS:
                        raise ValidationError("Formatul imaginii nu este acceptat.")
                    if image.width * image.height > 40_000_000:
                        raise ValidationError("Imaginea depășește limita de 40 megapixeli.")
                    image.verify()
            except (UnidentifiedImageError, OSError) as exc:
                raise ValidationError("Fișierul încărcat nu este o imagine validă.") from exc
    finally:
        upload.seek(position)
    return upload


def validate_csv_upload(upload):
    if Path(upload.name).suffix.lower() != ".csv":
        raise ValidationError("Importul METRO acceptă numai fișiere CSV.")
    if not upload.size or upload.size > MAX_CSV_SIZE:
        raise ValidationError("Fișierul CSV trebuie să aibă maximum 2 MB.")
    position = upload.tell()
    try:
        if b"\x00" in upload.read(4096):
            raise ValidationError("Fișierul CSV conține date binare invalide.")
    finally:
        upload.seek(position)
    return upload

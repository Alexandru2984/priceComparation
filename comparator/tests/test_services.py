from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from PIL import Image, ImageDraw, ImageFont

from comparator.models import Invoice, InvoiceLine, Product, Supplier
from comparator.services.invoices import process_invoice, sync_metro_offer_from_line
from comparator.services.matching import normalize_name, suggest_product
from comparator.services.ocr import extract_text
from comparator.services.parser import parse_heuristic


class ParserTests(TestCase):
    def test_parses_simple_romanian_invoice_lines(self):
        text = """Furnizor: SC DISTRIBUTIE SRL
1. CcaCola 2L - 10 bucati x 7,90 RON
2. Zahar Margaritar 1kg - 50 kg x 4.50 RON
TOTAL 304.00"""
        products = parse_heuristic(text)
        self.assertEqual(len(products), 2)
        self.assertEqual(products[0]["quantity"], Decimal("10"))
        self.assertEqual(products[0]["unit_size"], Decimal("2"))
        self.assertEqual(products[0]["base_unit"], "L")

    def test_normalizes_diacritics_and_packaging_words(self):
        self.assertEqual(normalize_name("Zahăr pungă 1 KG"), "zahar 1")

    def test_parses_receipt_line_with_line_total(self):
        products = parse_heuristic("Coca Cola 2L  2 x 7,90 15,80")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["quantity"], Decimal("2"))
        self.assertEqual(products[0]["line_total_gross"], Decimal("15.80"))


class MatchingTests(TestCase):
    def test_fuzzy_match_handles_abbreviation(self):
        product = Product.objects.create(name="Ulei floarea soarelui Floriol 1L", base_unit="L")
        match, score = suggest_product("Ulei Flr Soar Floriol 1L")
        self.assertEqual(match, product)
        self.assertGreaterEqual(score, 65)


class InvoiceProcessingTests(TestCase):
    @override_settings(OLLAMA_ENABLED=False)
    def test_processes_pasted_text_without_ocr_or_ollama(self):
        supplier = Supplier.objects.create(name="Distribuitor")
        Product.objects.create(name="Coca Cola 2L", base_unit="L")
        invoice = Invoice.objects.create(
            supplier=supplier,
            issued_at=date(2026, 7, 14),
            ocr_text="Coca Cola 2L - 10 bucati x 7.90 RON",
        )
        process_invoice(invoice)
        invoice.refresh_from_db()
        self.assertEqual(invoice.lines.count(), 1)
        self.assertEqual(invoice.status, Invoice.Status.REVIEW)
        self.assertEqual(invoice.lines.get().units_per_package, Decimal("1"))

    def test_confirmed_metro_receipt_updates_reference_price(self):
        supplier = Supplier.objects.create(name="METRO Test", is_metro=True)
        product = Product.objects.create(name="Ulei 1L", base_unit="L")
        invoice = Invoice.objects.create(
            supplier=supplier,
            document_type=Invoice.DocumentType.RECEIPT,
            number="B100",
            issued_at=date(2026, 7, 14),
        )
        line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name="ULEI 1L",
            quantity=2,
            units_per_package=1,
            unit_size=1,
            base_unit="L",
            unit_price_gross=Decimal("8.50"),
            matched_product=product,
            needs_review=False,
        )
        offer = sync_metro_offer_from_line(line)
        self.assertIsNotNone(offer)
        self.assertEqual(offer.price_gross, Decimal("8.50"))
        self.assertEqual(offer.source, "Bon fiscal METRO B100")


class OCRIntegrationTests(TestCase):
    def test_tesseract_reads_local_invoice_image(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "invoice.png"
            image = Image.new("RGB", (1400, 360), "white")
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 54)
            draw = ImageDraw.Draw(image)
            draw.text((40, 45), "FACTURA TEST", fill="black", font=font)
            draw.text((40, 140), "Coca Cola 2L  10 x 7.90 RON", fill="black", font=font)
            image.save(path)
            text = extract_text(path)
            self.assertIn("Coca", text)

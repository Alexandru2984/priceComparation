from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from PIL import Image, ImageDraw, ImageFont

from comparator.models import Invoice, InvoiceLine, Product, Supplier
from comparator.services.invoices import process_invoice, sync_metro_offer_from_line
from comparator.services.matching import apply_match, normalize_name, rank_product_candidates, suggest_product
from comparator.services.ocr import extract_text
from comparator.services.parser import parse_heuristic, parse_invoice_text


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

    def test_normalization_preserves_zero_digits_in_gramage(self):
        self.assertEqual(normalize_name("Iaurt 500 g"), "iaurt 500")

    def test_parses_receipt_line_with_line_total(self):
        products = parse_heuristic("Coca Cola 2L  2 x 7,90 15,80")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["quantity"], Decimal("2"))
        self.assertEqual(products[0]["line_total_gross"], Decimal("15.80"))

    def test_parses_common_receipt_columns_without_multiplication_sign(self):
        products = parse_heuristic("5941234567890 IAURT NATURAL 400G 2 4,50 9,00 A")
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["ean"], "5941234567890")
        self.assertEqual(products[0]["original_name"], "IAURT NATURAL 400G")
        self.assertEqual(products[0]["quantity"], Decimal("2"))
        self.assertEqual(products[0]["unit_price_gross"], Decimal("4.50"))

    @override_settings(OLLAMA_ENABLED=True)
    @patch("comparator.services.parser.parse_with_ollama")
    def test_hybrid_parser_completes_missing_lines_and_deduplicates(self, model_parser):
        model_parser.return_value = [
            {
                "original_name": "Coca Cola 2L",
                "ean": "",
                "quantity": 2,
                "units_per_package": 1,
                "unit_size": 2,
                "base_unit": "L",
                "unit_price_gross": 7.9,
                "vat_rate": 0,
                "line_total_gross": 15.8,
                "discount_gross": 0,
                "deposit_gross": 0,
            },
            {
                "original_name": "Pate Bucegi",
                "ean": "",
                "quantity": 1,
                "units_per_package": 1,
                "unit_size": 1,
                "base_unit": "BUC",
                "unit_price_gross": 3.99,
                "vat_rate": 0,
                "line_total_gross": 3.99,
                "discount_gross": 0,
                "deposit_gross": 0,
            },
        ]
        text = "Coca Cola 2L 2 x 7,90 15,80\nPATE BUCEGI PRET 3,99 TOTAL 3,99"

        products, parser_name, warning = parse_invoice_text(text)

        self.assertEqual(parser_name, "hybrid")
        self.assertIsNone(warning)
        self.assertEqual([item["original_name"] for item in products], ["Coca Cola 2L", "Pate Bucegi"])


class MatchingTests(TestCase):
    def test_fuzzy_match_handles_abbreviation(self):
        product = Product.objects.create(name="Ulei floarea soarelui Floriol 1L", base_unit="L")
        match, score = suggest_product("Ulei Flr Soar Floriol 1L")
        self.assertEqual(match, product)
        self.assertGreaterEqual(score, 65)

    def test_packaging_score_prefers_correct_size(self):
        small = Product.objects.create(name="Coca Cola SGR 0,5 L", base_unit="L")
        large = Product.objects.create(name="Coca Cola SGR 2 L", base_unit="L")
        candidates = rank_product_candidates(
            "Coca Cola 2L",
            base_unit="L",
            unit_size=Decimal("2"),
        )
        self.assertEqual(candidates[0]["product"], large)
        self.assertGreater(candidates[0]["score"], next(row["score"] for row in candidates if row["product"] == small))

    def test_ambiguous_match_is_left_for_review(self):
        supplier = Supplier.objects.create(name="Furnizor ambiguu")
        Product.objects.create(name="Lapte integral Brand A 1 L", base_unit="L")
        Product.objects.create(name="Lapte integral Brand B 1 L", base_unit="L")
        invoice = Invoice.objects.create(supplier=supplier, issued_at=date(2026, 8, 25))
        line = InvoiceLine(
            invoice=invoice,
            original_name="Lapte integral 1 L",
            quantity=1,
            units_per_package=1,
            unit_size=1,
            base_unit="L",
            unit_price_gross=5,
        )
        apply_match(line)
        self.assertTrue(line.needs_review)
        self.assertEqual(len(line.match_candidates), 2)
        self.assertLess(line.match_gap, 7)


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

    @patch("comparator.services.ocr._tesseract_image")
    @patch("pypdfium2.PdfDocument")
    def test_digital_pdf_uses_embedded_text_without_tesseract(self, pdf_document, tesseract):
        embedded = "FACTURA DIGITALA\n" + "Produs alimentar 2 x 7,90 15,80\n" * 5
        text_page = MagicMock()
        text_page.get_text_range.return_value = embedded
        page = MagicMock()
        page.get_textpage.return_value = text_page
        pdf_document.return_value = [page]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "digital.pdf"
            path.write_bytes(b"%PDF-test")

            text = extract_text(path)

        self.assertEqual(text, embedded)
        tesseract.assert_not_called()
        text_page.close.assert_called_once()

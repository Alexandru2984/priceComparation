from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from comparator.models import Invoice, InvoiceLine, InvoiceRevision, MetroOffer, Product, Supplier, SupplierOffer
from comparator.services.invoices import (
    create_invoice_revision,
    delete_invoice_line,
    process_invoice,
    sync_metro_offer_from_line,
)


class InvoiceRevisionTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username="revision-admin", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.staff)
        self.supplier = Supplier.objects.create(name="Furnizor versiuni")

    @override_settings(OLLAMA_ENABLED=False)
    def test_reprocessing_saves_existing_lines_before_replacement(self):
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 9),
            ocr_text="Produs nou - 1 buc x 3.00 RON",
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="Corecție manuală veche",
            quantity=2,
            unit_price_gross=Decimal("8.50"),
            needs_review=False,
        )

        process_invoice(invoice, created_by=self.staff)

        revision = invoice.revisions.get()
        self.assertEqual(revision.reason, InvoiceRevision.Reason.OCR_REPROCESS)
        self.assertEqual(revision.created_by, self.staff)
        self.assertEqual(revision.line_count, 1)
        self.assertEqual(revision.snapshot["lines"][0]["original_name"], "Corecție manuală veche")
        self.assertEqual(invoice.lines.get().original_name, "Produs nou")

    def test_restore_round_trip_preserves_prices_matches_and_creates_undo_revision(self):
        product = Product.objects.create(name="Produs restaurat", base_unit="BUC")
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at=date(2026, 8, 9),
            status=Invoice.Status.PROCESSED,
            ocr_text="TEXT VECHI",
        )
        old_line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name="LINIE VECHE",
            quantity=2,
            unit_price_gross=Decimal("4.25"),
            matched_product=product,
            match_score=100,
            needs_review=False,
        )
        revision = create_invoice_revision(invoice, InvoiceRevision.Reason.OCR_REPROCESS, self.staff)
        old_line.delete()
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="LINIE CURENTĂ",
            quantity=1,
            unit_price_gross=Decimal("99"),
            needs_review=True,
        )
        invoice.ocr_text = "TEXT CURENT"
        invoice.status = Invoice.Status.REVIEW
        invoice.save(update_fields=["ocr_text", "status"])

        response = self.client.post(
            f"/app/facturi/{invoice.pk}/versiuni/{revision.pk}/restaureaza/"
        )

        self.assertEqual(response.status_code, 302)
        restored = invoice.lines.get()
        invoice.refresh_from_db()
        self.assertEqual(restored.original_name, "LINIE VECHE")
        self.assertEqual(restored.unit_price_gross, Decimal("4.25"))
        self.assertEqual(restored.matched_product, product)
        self.assertFalse(restored.needs_review)
        self.assertEqual(invoice.ocr_text, "TEXT VECHI")
        self.assertEqual(invoice.status, Invoice.Status.PROCESSED)
        self.assertTrue(SupplierOffer.objects.filter(invoice_line=restored).exists())
        self.assertEqual(invoice.revisions.count(), 2)
        self.assertEqual(invoice.revisions.first().reason, InvoiceRevision.Reason.RESTORE)

    def test_restoring_metro_revision_replaces_current_derived_offer(self):
        metro = Supplier.objects.create(name="METRO versiuni", is_metro=True)
        old_product = Product.objects.create(name="Produs METRO vechi", base_unit="BUC")
        current_product = Product.objects.create(name="Produs METRO curent", base_unit="BUC")
        invoice = Invoice.objects.create(
            supplier=metro,
            number="REV-METRO",
            issued_at=date(2026, 8, 9),
            status=Invoice.Status.PROCESSED,
        )
        old_line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name=old_product.name,
            quantity=1,
            unit_price_gross=Decimal("7"),
            matched_product=old_product,
            match_score=100,
            needs_review=False,
        )
        sync_metro_offer_from_line(old_line)
        revision = create_invoice_revision(invoice, InvoiceRevision.Reason.OCR_REPROCESS, self.staff)
        delete_invoice_line(old_line)
        current_line = InvoiceLine.objects.create(
            invoice=invoice,
            original_name=current_product.name,
            quantity=1,
            unit_price_gross=Decimal("12"),
            matched_product=current_product,
            match_score=100,
            needs_review=False,
        )
        sync_metro_offer_from_line(current_line)

        self.client.post(f"/app/facturi/{invoice.pk}/versiuni/{revision.pk}/restaureaza/")

        self.assertTrue(MetroOffer.objects.filter(product=old_product, price_gross=Decimal("7")).exists())
        self.assertFalse(MetroOffer.objects.filter(product=current_product).exists())

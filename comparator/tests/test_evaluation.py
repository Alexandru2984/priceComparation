import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from comparator.models import DocumentPage, Invoice, InvoiceLine, Supplier
from comparator.services.evaluation import evaluate_confirmed_documents, evaluate_manifest


class DocumentEvaluationTests(SimpleTestCase):
    def test_sample_manifest_has_perfect_deterministic_baseline(self):
        report = evaluate_manifest(Path("sample_data/evaluation_manifest.json"))

        self.assertEqual(report["metrics"]["precision"], 100)
        self.assertEqual(report["metrics"]["recall"], 100)
        self.assertEqual(report["metrics"]["price_accuracy"], 100)
        self.assertEqual(report["metrics"]["unit_accuracy"], 100)

    def test_command_can_write_machine_readable_report(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            call_command(
                "evaluate_documents",
                Path("sample_data/evaluation_manifest.json"),
                output_json=output,
                min_recall=95,
                min_price_accuracy=95,
                verbosity=0,
            )
            payload = json.loads(output.read_text())
            self.assertEqual(payload["totals"]["expected"], 3)

    def test_manifest_cannot_read_a_document_outside_its_directory(self):
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            manifest = directory / "manifest.json"
            manifest.write_text(json.dumps({"cases": [{"name": "escape", "file": "../secret.jpg", "expected": []}]}))
            with self.assertRaisesMessage(ValueError, "iese din director"):
                evaluate_manifest(manifest)


class RealDocumentEvaluationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            "ocr-operator", password="A-test-password-2026!", is_staff=True
        )
        self.client.force_login(self.user)
        self.supplier = Supplier.objects.create(name="Furnizor calibrare")

    def _invoice(self, needs_review=False):
        invoice = Invoice.objects.create(
            supplier=self.supplier,
            issued_at="2026-08-31",
            ocr_text="Lapte 1L - 2 bucati x 6,50 RON",
            evaluation_sample=True,
        )
        InvoiceLine.objects.create(
            invoice=invoice,
            original_name="Lapte 1L",
            quantity=2,
            unit_size=1,
            base_unit="L",
            unit_price_gross="6.50",
            needs_review=needs_review,
        )
        DocumentPage.objects.create(
            invoice=invoice,
            file="documents/test.jpg",
            page_order=1,
            ocr_text=invoice.ocr_text,
            ocr_quality_score=88,
            ocr_strategy="ORIGINAL",
        )
        return invoice

    def test_confirmed_document_is_used_as_local_ground_truth(self):
        invoice = self._invoice()

        report = evaluate_confirmed_documents(
            Invoice.objects.filter(pk=invoice.pk).prefetch_related("lines", "pages")
        )

        self.assertEqual(report["evaluated_count"], 1)
        self.assertEqual(report["metrics"]["recall"], 100)
        self.assertEqual(report["metrics"]["price_accuracy"], 100)
        self.assertEqual(report["average_ocr_quality"], 88)

    def test_document_with_unconfirmed_lines_remains_pending(self):
        invoice = self._invoice(needs_review=True)

        report = evaluate_confirmed_documents(
            Invoice.objects.filter(pk=invoice.pk).prefetch_related("lines", "pages")
        )

        self.assertEqual(report["evaluated_count"], 0)
        self.assertEqual(report["pending_count"], 1)
        self.assertIn("linii de verificat", report["rows"][0]["reason"])

    def test_operator_can_toggle_sample_and_open_dashboard(self):
        invoice = self._invoice()
        invoice.evaluation_sample = False
        invoice.save(update_fields=["evaluation_sample"])

        response = self.client.post(f"/app/facturi/{invoice.pk}/calibrare-ocr/")

        self.assertRedirects(response, f"/app/facturi/{invoice.pk}/")
        invoice.refresh_from_db()
        self.assertTrue(invoice.evaluation_sample)
        dashboard = self.client.get("/app/calibrare-ocr/")
        self.assertContains(dashboard, "100,0%")

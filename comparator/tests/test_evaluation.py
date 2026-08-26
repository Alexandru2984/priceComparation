import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import SimpleTestCase

from comparator.services.evaluation import evaluate_manifest


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

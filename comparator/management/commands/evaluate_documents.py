import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from comparator.services.evaluation import evaluate_manifest


class Command(BaseCommand):
    help = "Evaluează local OCR-ul/parserul pe documente etichetate într-un manifest JSON."

    def add_arguments(self, parser):
        parser.add_argument("manifest", type=Path)
        parser.add_argument("--output-json", type=Path)
        parser.add_argument("--min-recall", type=float, default=0)
        parser.add_argument("--min-price-accuracy", type=float, default=0)

    def handle(self, *args, **options):
        for option in ("min_recall", "min_price_accuracy"):
            if not 0 <= options[option] <= 100:
                raise CommandError(f"--{option.replace('_', '-')} trebuie să fie între 0 și 100.")
        try:
            report = evaluate_manifest(options["manifest"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise CommandError(str(exc)) from exc
        serialized = json.dumps(report, ensure_ascii=False, indent=2)
        if options["output_json"]:
            options["output_json"].write_text(serialized + "\n", encoding="utf-8")
        self.stdout.write(serialized)
        if report["metrics"]["recall"] < options["min_recall"]:
            raise CommandError(f"Recall {report['metrics']['recall']}% este sub pragul cerut.")
        if report["metrics"]["price_accuracy"] < options["min_price_accuracy"]:
            raise CommandError(f"Acuratețea prețului {report['metrics']['price_accuracy']}% este sub pragul cerut.")

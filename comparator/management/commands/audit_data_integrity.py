from django.core.management.base import BaseCommand, CommandError

from comparator.services.integrity import audit_data_integrity


class Command(BaseCommand):
    help = "Auditează consistența datelor care alimentează comparațiile și stocul."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-on-warnings",
            action="store_true",
            help="Întoarce cod de eroare și când sunt găsite numai avertismente.",
        )

    def handle(self, *args, **options):
        report = audit_data_integrity()
        for check in report["checks"]:
            status = check["severity"] if check["count"] else "OK"
            line = f"[{status}] {check['name']}: {check['count']}"
            if check["examples"]:
                line += f" · exemple: {', '.join(check['examples'])}"
            self.stdout.write(line)

        summary = f"Audit finalizat: {report['error_count']} erori, {report['warning_count']} avertismente."
        if report["error_count"] or (options["fail_on_warnings"] and report["warning_count"]):
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))

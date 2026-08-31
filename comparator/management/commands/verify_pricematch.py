from django.contrib.auth import get_user_model
from django.core.checks import ERROR, run_checks
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from comparator.services.integrity import audit_data_integrity


class Command(BaseCommand):
    help = "Rulează poarta unică de verificare înainte de o versiune sau publicare."

    def add_arguments(self, parser):
        parser.add_argument(
            "--deploy",
            action="store_true",
            help="Include verificările Django și PriceMatch stricte pentru producție.",
        )
        parser.add_argument(
            "--fail-on-warnings",
            action="store_true",
            help="Blochează și pentru avertismentele auditului de date.",
        )

    def handle(self, *args, **options):
        failures = []
        messages = run_checks(include_deployment_checks=options["deploy"])
        check_errors = [message for message in messages if message.level >= ERROR]
        for message in messages:
            self.stdout.write(f"[{message.id or 'Django'}] {message.msg}")
        if check_errors:
            failures.append(f"{len(check_errors)} erori de configurare")

        executor = MigrationExecutor(connection)
        pending = executor.migration_plan(executor.loader.graph.leaf_nodes())
        self.stdout.write(f"[{'ERROR' if pending else 'OK'}] Migrații neaplicate: {len(pending)}")
        if pending:
            failures.append(f"{len(pending)} migrații neaplicate")

        admin_count = (
            get_user_model()
            .objects.filter(
                is_active=True,
                is_staff=True,
                is_superuser=True,
            )
            .count()
        )
        self.stdout.write(f"[{'OK' if admin_count else 'ERROR'}] Administratori activi: {admin_count}")
        if not admin_count:
            failures.append("niciun administrator activ")

        integrity = audit_data_integrity()
        self.stdout.write(
            f"[{'ERROR' if integrity['error_count'] else 'OK'}] Integritate date: "
            f"{integrity['error_count']} erori, {integrity['warning_count']} avertismente"
        )
        if integrity["error_count"]:
            failures.append(f"{integrity['error_count']} erori de integritate")
        if options["fail_on_warnings"] and integrity["warning_count"]:
            failures.append(f"{integrity['warning_count']} avertismente de integritate")

        if failures:
            raise CommandError("Verificarea a eșuat: " + "; ".join(failures) + ".")
        self.stdout.write(self.style.SUCCESS("PriceMatch este pregătit pentru pasul următor."))

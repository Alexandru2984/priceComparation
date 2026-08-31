from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from .disable_staff_user import revoke_user_sessions


class Command(BaseCommand):
    help = "Revocă toate sesiunile active ale unui cont PriceMatch."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirmă explicit delogarea contului de pe toate dispozitivele.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Repetă comanda cu --confirm pentru a revoca sesiunile.")
        users = get_user_model()
        try:
            user = users.objects.get(username__iexact=options["username"].strip())
        except users.DoesNotExist as exc:
            raise CommandError("Utilizatorul nu există.") from exc
        if not user.is_staff:
            raise CommandError("Contul nu este un cont PriceMatch.")
        revoked = revoke_user_sessions(user)
        self.stdout.write(
            self.style.SUCCESS(
                f"Au fost revocate {revoked} sesiuni pentru «{user.username}»."
            )
        )

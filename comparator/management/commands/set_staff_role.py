from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from .disable_staff_user import revoke_user_sessions


class Command(BaseCommand):
    help = "Schimbă rolul unui cont PriceMatch între operator și admin."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("role", choices=("operator", "admin"))
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirmă schimbarea rolului și revocarea sesiunilor.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Repetă comanda cu --confirm pentru a schimba rolul.")
        users = get_user_model()
        try:
            user = users.objects.get(username__iexact=options["username"].strip())
        except users.DoesNotExist as exc:
            raise CommandError("Utilizatorul nu există.") from exc
        if not user.is_staff:
            raise CommandError("Contul nu este un cont PriceMatch.")

        make_admin = options["role"] == "admin"
        if user.is_superuser == make_admin:
            self.stdout.write(
                self.style.WARNING(
                    f"Contul «{user.username}» are deja rolul {options['role']}."
                )
            )
            return
        if user.is_superuser and user.is_active and not users.objects.filter(
            is_active=True,
            is_superuser=True,
        ).exclude(pk=user.pk).exists():
            raise CommandError(
                "Ultimul superuser activ nu poate fi retrogradat. "
                "Promovează mai întâi alt cont."
            )

        user.is_superuser = make_admin
        user.save(update_fields=["is_superuser"])
        revoked = revoke_user_sessions(user)
        self.stdout.write(
            self.style.SUCCESS(
                f"Rolul contului «{user.username}» este acum {options['role']}; "
                f"{revoked} sesiuni revocate."
            )
        )

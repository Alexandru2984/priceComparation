from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Reactivează un cont PriceMatch existent, fără a crea sau promova utilizatori."

    def add_arguments(self, parser):
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, **options):
        users = get_user_model()
        try:
            user = users.objects.get(username__iexact=options["username"].strip())
        except users.DoesNotExist as exc:
            raise CommandError("Utilizatorul nu există.") from exc
        if not user.is_staff:
            raise CommandError(
                "Contul nu este un cont PriceMatch. Folosește create_staff_user pentru acces nou."
            )
        if user.is_active:
            self.stdout.write(
                self.style.WARNING(f"Contul «{user.username}» este deja activ.")
            )
            return
        user.is_active = True
        user.save(update_fields=["is_active"])
        self.stdout.write(self.style.SUCCESS(f"Contul «{user.username}» a fost activat."))

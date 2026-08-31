import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


class Command(BaseCommand):
    help = "Creează interactiv un cont PriceMatch staff; parola nu poate fi transmisă în linia de comandă."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--email", default="")
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Acordă și acces complet Django; implicit contul are acces doar la aplicația PriceMatch.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        users = get_user_model()
        username = users.normalize_username(options["username"].strip())
        if not username:
            raise CommandError("Numele utilizatorului nu poate fi gol.")
        if users.objects.filter(username__iexact=username).exists():
            raise CommandError(f"Utilizatorul «{username}» există deja.")

        user = users(
            username=username,
            email=options["email"].strip(),
            is_active=True,
            is_staff=True,
            is_superuser=options["superuser"],
        )
        try:
            user.full_clean(exclude=["password", "last_login", "date_joined"])
        except ValidationError as exc:
            raise CommandError("Date de cont invalide: " + "; ".join(exc.messages)) from exc

        password = getpass.getpass("Parolă: ")
        confirmation = getpass.getpass("Confirmă parola: ")
        if password != confirmation:
            raise CommandError("Parolele nu coincid; contul nu a fost creat.")
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError("Parolă respinsă: " + "; ".join(exc.messages)) from exc

        user.set_password(password)
        user.save()
        role = "superuser" if user.is_superuser else "staff"
        self.stdout.write(self.style.SUCCESS(
            f"Contul «{username}» a fost creat ca {role}. La primul login va configura MFA."
        ))

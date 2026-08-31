from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from .disable_staff_user import revoke_user_sessions


class Command(BaseCommand):
    help = "Șterge dispozitivele/codurile MFA ale unui cont și revocă sesiunile pentru reînrolare."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Confirmă explicit resetarea MFA și revocarea sesiunilor.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not options["confirm"]:
            raise CommandError("Repetă comanda cu --confirm pentru a reseta MFA.")
        users = get_user_model()
        try:
            user = users.objects.get(username__iexact=options["username"].strip())
        except users.DoesNotExist as exc:
            raise CommandError("Utilizatorul nu există.") from exc
        totp_count, _ = TOTPDevice.objects.filter(user=user).delete()
        static_count, _ = StaticDevice.objects.filter(user=user).delete()
        revoked = revoke_user_sessions(user)
        self.stdout.write(self.style.SUCCESS(
            f"MFA resetat pentru «{user.username}»: {totp_count + static_count} dispozitive/coduri "
            f"șterse și {revoked} sesiuni revocate."
        ))

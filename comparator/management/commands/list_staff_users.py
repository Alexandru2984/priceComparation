from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django_otp.plugins.otp_totp.models import TOTPDevice


class Command(BaseCommand):
    help = "Listează conturile PriceMatch și starea accesului/MFA, fără date sensibile."

    def handle(self, *args, **options):
        users = get_user_model().objects.order_by("username")
        if not users.exists():
            self.stdout.write("Nu există conturi.")
            return
        self.stdout.write("username\tactiv\tstaff\tsuperuser\tmfa")
        confirmed_mfa = set(
            TOTPDevice.objects.filter(confirmed=True).values_list("user_id", flat=True)
        )
        for user in users:
            values = (
                user.username,
                "da" if user.is_active else "nu",
                "da" if user.is_staff else "nu",
                "da" if user.is_superuser else "nu",
                "configurat" if user.pk in confirmed_mfa else "lipsește",
            )
            self.stdout.write("\t".join(values))

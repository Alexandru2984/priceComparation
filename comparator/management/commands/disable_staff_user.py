from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


def revoke_user_sessions(user):
    revoked = 0
    for session in Session.objects.all().iterator():
        if str(session.get_decoded().get("_auth_user_id")) == str(user.pk):
            session.delete()
            revoked += 1
    return revoked


class Command(BaseCommand):
    help = "Dezactivează un cont PriceMatch și revocă imediat toate sesiunile lui."

    def add_arguments(self, parser):
        parser.add_argument("username")

    @transaction.atomic
    def handle(self, *args, **options):
        users = get_user_model()
        try:
            user = users.objects.get(username__iexact=options["username"].strip())
        except users.DoesNotExist as exc:
            raise CommandError("Utilizatorul nu există.") from exc
        if not user.is_active:
            self.stdout.write(self.style.WARNING(f"Contul «{user.username}» este deja dezactivat."))
            return
        if user.is_superuser and not users.objects.filter(
            is_active=True,
            is_superuser=True,
        ).exclude(pk=user.pk).exists():
            raise CommandError(
                "Ultimul superuser activ nu poate fi dezactivat. Creează mai întâi alt superuser."
            )
        user.is_active = False
        user.save(update_fields=["is_active"])
        revoked = revoke_user_sessions(user)
        self.stdout.write(self.style.SUCCESS(
            f"Contul «{user.username}» a fost dezactivat; {revoked} sesiuni revocate."
        ))

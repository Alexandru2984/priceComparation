from django.core.management.base import BaseCommand, CommandError

from comparator.services.notifications import send_triggered_price_alerts, webpush_configured


class Command(BaseCommand):
    help = "Trimite o singură dată notificările Web Push pentru alertele de preț nou declanșate."

    def handle(self, *args, **options):
        if not webpush_configured():
            raise CommandError("Configurează cheile WEBPUSH_VAPID_PRIVATE_KEY și WEBPUSH_VAPID_PUBLIC_KEY.")
        result = send_triggered_price_alerts()
        self.stdout.write(
            self.style.SUCCESS(
                f"Alerte notificate: {result['alerts']}; mesaje livrate: {result['messages']}."
            )
        )

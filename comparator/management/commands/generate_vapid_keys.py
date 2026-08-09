import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Generează local cheia privată și cheia publică VAPID pentru notificările Web Push."

    def add_arguments(self, parser):
        parser.add_argument("--output", type=Path, default=Path(settings.BASE_DIR) / "data" / "vapid_private.pem")
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        destination = options["output"].expanduser().resolve()
        if destination.exists() and not options["force"]:
            raise CommandError("Cheia există deja. Nu o suprascrie: abonamentele curente ar deveni invalide.")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        private_key = ec.generate_private_key(ec.SECP256R1())
        destination.write_bytes(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        destination.chmod(0o600)
        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        public_key = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
        self.stdout.write(self.style.SUCCESS(f"Cheie privată: {destination}"))
        self.stdout.write(f"WEBPUSH_VAPID_PRIVATE_KEY={destination}")
        self.stdout.write(f"WEBPUSH_VAPID_PUBLIC_KEY={public_key}")

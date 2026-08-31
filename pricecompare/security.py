from ipaddress import ip_address

from django.conf import settings


def get_client_ip_address(request):
    """Returnează IP-ul rescris de proxy numai când cererea vine de la un proxy aprobat."""
    remote_ip = request.META.get("REMOTE_ADDR")
    forwarded_ip = request.META.get("HTTP_X_REAL_IP")
    if (
        settings.TRUST_REVERSE_PROXY
        and remote_ip in settings.TRUSTED_REVERSE_PROXY_IPS
        and forwarded_ip
    ):
        try:
            return str(ip_address(forwarded_ip))
        except ValueError:
            pass
    return remote_ip

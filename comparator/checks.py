from ipaddress import ip_address
from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, Tags, Warning, register


def _loopback_hostname(hostname):
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


@register(Tags.security, deploy=True)
def pricematch_deployment_security(app_configs, **kwargs):
    if not settings.PRODUCTION:
        return []

    issues = []
    engine = settings.DATABASES["default"]["ENGINE"]
    if engine != "django.db.backends.postgresql":
        issues.append(
            Error(
                "Producția PriceMatch trebuie să folosească PostgreSQL.",
                hint="Setează DB_ENGINE=postgresql și o bază dedicată.",
                id="pricematch.E001",
            )
        )
    if not settings.ALLOWED_HOSTS or "*" in settings.ALLOWED_HOSTS:
        issues.append(
            Error(
                "DJANGO_ALLOWED_HOSTS trebuie să enumere explicit hosturile de producție.",
                id="pricematch.E002",
            )
        )
    insecure_origins = [
        origin for origin in settings.CSRF_TRUSTED_ORIGINS if urlparse(origin).scheme != "https"
    ]
    if insecure_origins:
        issues.append(
            Error(
                "Originile CSRF de producție trebuie să folosească HTTPS.",
                obj=", ".join(insecure_origins),
                id="pricematch.E003",
            )
        )
    if settings.TRUST_REVERSE_PROXY:
        invalid_proxy_ips = []
        for value in settings.TRUSTED_REVERSE_PROXY_IPS:
            try:
                ip_address(value)
            except ValueError:
                invalid_proxy_ips.append(value)
        if not settings.TRUSTED_REVERSE_PROXY_IPS or invalid_proxy_ips:
            issues.append(
                Error(
                    "Lista proxy-urilor de încredere este goală sau conține IP-uri invalide.",
                    obj=", ".join(invalid_proxy_ips),
                    id="pricematch.E004",
                )
            )
    ollama = urlparse(settings.OLLAMA_URL)
    if settings.OLLAMA_ENABLED and (
        ollama.scheme not in {"http", "https"} or not _loopback_hostname(ollama.hostname)
    ):
        issues.append(
            Error(
                "Ollama activ trebuie să fie accesibil numai prin loopback.",
                hint="Folosește http://127.0.0.1:11434 și nu expune Ollama în rețea.",
                id="pricematch.E005",
            )
        )
    metro = urlparse(settings.METRO_START_URL)
    if metro.scheme != "https" or not metro.hostname or not (
        metro.hostname == "metro.ro" or metro.hostname.endswith(".metro.ro")
    ):
        issues.append(
            Error(
                "METRO_START_URL trebuie să fie un URL HTTPS de pe domeniul metro.ro.",
                id="pricematch.E006",
            )
        )
    if settings.DEPLOYMENT_ENVIRONMENT != "production":
        issues.append(
            Error(
                "PRICEMATCH_ENVIRONMENT trebuie să fie production când DJANGO_PRODUCTION=1.",
                id="pricematch.E007",
            )
        )
    if settings.METRO_SELENIUM_ENABLED:
        issues.append(
            Warning(
                "Selenium METRO este activ în producție.",
                hint="Ține METRO_SELENIUM_ENABLED=0 și activează-l numai controlat.",
                id="pricematch.W001",
            )
        )
    return issues

import os
import sys
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
PRODUCTION = os.getenv("DJANGO_PRODUCTION", "1" if not DEBUG else "0") == "1"
DEPLOYMENT_ENVIRONMENT = os.getenv(
    "PRICEMATCH_ENVIRONMENT", "production" if PRODUCTION else "local"
).strip().lower()
TESTING = os.getenv("DJANGO_TESTING", "0") == "1" or "test" in sys.argv
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()
]

if PRODUCTION and SECRET_KEY in {"dev-only-change-me", "schimba-ma", "local-pricecompare"}:
    raise ImproperlyConfigured("Setează un DJANGO_SECRET_KEY aleator înainte de publicare.")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_otp",
    "django_otp.plugins.otp_static",
    "django_otp.plugins.otp_totp",
    "two_factor",
    "axes",
    "comparator",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "pricecompare.middleware.ActivityAuditMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "pricecompare.middleware.AdminMFAEnforcementMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
    "pricecompare.middleware.SecurityHeadersMiddleware",
]

ROOT_URLCONF = "pricecompare.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "pricecompare.context_processors.deployment_environment",
            ],
        },
    }
]

WSGI_APPLICATION = "pricecompare.wsgi.application"
ASGI_APPLICATION = "pricecompare.asgi.application"

if os.getenv("DB_ENGINE", "sqlite").lower() in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "pricecompare"),
            "USER": os.getenv("DB_USER", "pricecompare"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(os.getenv("SQLITE_PATH", BASE_DIR / "db.sqlite3")),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "two_factor:login"
LOGIN_REDIRECT_URL = "/app/"
LOGOUT_REDIRECT_URL = "/"
OTP_LOGIN_URL = "two_factor:login"
OTP_TOTP_ISSUER = "PriceMatch"
MFA_REQUIRED = os.getenv("MFA_REQUIRED", "1" if PRODUCTION else "0") == "1" and not TESTING
TWO_FACTOR_PATCH_ADMIN = True

# Protejează atât Django Admin, cât și fluxul principal django-two-factor-auth.
AXES_ONLY_ADMIN_SITE = False
AXES_FAILURE_LIMIT = int(os.getenv("AXES_FAILURE_LIMIT", "5"))
AXES_COOLOFF_TIME = 1
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = PRODUCTION
SESSION_COOKIE_AGE = int(os.getenv("SESSION_COOKIE_AGE", "28800"))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = PRODUCTION
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_SSL_REDIRECT = PRODUCTION and not TESTING and os.getenv("DJANGO_SECURE_SSL_REDIRECT", "1") == "1"
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDS", "3600")) if PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = PRODUCTION and os.getenv("DJANGO_HSTS_INCLUDE_SUBDOMAINS", "0") == "1"
SECURE_HSTS_PRELOAD = PRODUCTION and os.getenv("DJANGO_HSTS_PRELOAD", "0") == "1"
X_FRAME_OPTIONS = "DENY"
TRUST_REVERSE_PROXY = os.getenv("DJANGO_TRUST_PROXY", "0") == "1"
TRUSTED_REVERSE_PROXY_IPS = {
    ip.strip()
    for ip in os.getenv("DJANGO_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if ip.strip()
}
if TRUST_REVERSE_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    AXES_CLIENT_IP_CALLABLE = "pricecompare.security.get_client_ip_address"
LANGUAGE_CODE = "ro-ro"
TIME_ZONE = "Europe/Bucharest"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 55 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 12
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:4b")
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", "1") == "1"
OCR_LANGUAGE = os.getenv("OCR_LANGUAGE", "ron+eng")
MATCH_AUTO_THRESHOLD = int(os.getenv("MATCH_AUTO_THRESHOLD", "82"))
MATCH_REVIEW_THRESHOLD = int(os.getenv("MATCH_REVIEW_THRESHOLD", "65"))
MATCH_AMBIGUITY_GAP = int(os.getenv("MATCH_AMBIGUITY_GAP", "7"))
METRO_START_URL = os.getenv("METRO_START_URL", "https://produse.metro.ro/shop")
METRO_BROWSER_PROFILE = Path(os.getenv("METRO_BROWSER_PROFILE", BASE_DIR / "data" / "metro_chrome_profile"))
METRO_SCRAPE_TIMEOUT_MINUTES = int(os.getenv("METRO_SCRAPE_TIMEOUT_MINUTES", "20"))
_LEGACY_METRO_SCRAPER_ENABLED = os.getenv("METRO_SCRAPER_ENABLED")
METRO_API_ENABLED = os.getenv("METRO_API_ENABLED", "1") == "1"
METRO_SELENIUM_ENABLED = os.getenv(
    "METRO_SELENIUM_ENABLED",
    _LEGACY_METRO_SCRAPER_ENABLED
    if _LEGACY_METRO_SCRAPER_ENABLED is not None
    else ("0" if PRODUCTION else "1"),
) == "1"
METRO_STORE_QUERY = os.getenv("METRO_STORE_QUERY", "")
PREFERRED_METRO_STORE = os.getenv("PREFERRED_METRO_STORE", "")
METRO_AUTOMATION_ENABLED = os.getenv("METRO_AUTOMATION_ENABLED", "0") == "1"
METRO_FULL_SCAN_INTERVAL_DAYS = int(os.getenv("METRO_FULL_SCAN_INTERVAL_DAYS", "7"))
METRO_TARGETED_SCAN_INTERVAL_HOURS = int(os.getenv("METRO_TARGETED_SCAN_INTERVAL_HOURS", "24"))
METRO_TARGETED_SCAN_MAX_PRODUCTS = int(os.getenv("METRO_TARGETED_SCAN_MAX_PRODUCTS", "150"))
METRO_PRICE_ANOMALY_PERCENT = Decimal(os.getenv("METRO_PRICE_ANOMALY_PERCENT", "40"))
SUPPLIER_PRICE_MAX_AGE_DAYS = int(os.getenv("SUPPLIER_PRICE_MAX_AGE_DAYS", "90"))
ACTIVITY_LOG_RETENTION_DAYS = int(os.getenv("ACTIVITY_LOG_RETENTION_DAYS", "365"))
TECHNICAL_DATA_RETENTION_DAYS = int(os.getenv("TECHNICAL_DATA_RETENTION_DAYS", "30"))
INVOICE_REVISION_LIMIT = int(os.getenv("INVOICE_REVISION_LIMIT", "10"))
WEBPUSH_VAPID_PRIVATE_KEY = os.getenv("WEBPUSH_VAPID_PRIVATE_KEY", "")
WEBPUSH_VAPID_PUBLIC_KEY = os.getenv("WEBPUSH_VAPID_PUBLIC_KEY", "")
WEBPUSH_VAPID_SUBJECT = os.getenv("WEBPUSH_VAPID_SUBJECT", "mailto:admin@pricematch.local")
WEBPUSH_ALLOWED_HOSTS = [
    host.strip().lower()
    for host in os.getenv(
        "WEBPUSH_ALLOWED_HOSTS",
        "fcm.googleapis.com,push.services.mozilla.com,notify.windows.com,push.apple.com",
    ).split(",")
    if host.strip()
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "axes": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "comparator": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

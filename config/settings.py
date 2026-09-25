"""Django settings, configured exclusively through environment variables (12-factor)."""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

ENVIRONMENT = os.environ.get("DJANGO_ENV", "development")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ImproperlyConfigured(f"Environment variable {name} is required.")
    return value


if ENVIRONMENT == "production":
    SECRET_KEY = _required("DJANGO_SECRET_KEY")
else:
    SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-development-only")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "ledger.infrastructure.apps.LedgerInfrastructureConfig",
    "ledger.presentation.apps.LedgerPresentationConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "ledger.presentation.api.middleware.DomainExceptionMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "ledger"),
        "USER": os.environ.get("POSTGRES_USER", "ledger"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "ledger"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # Transactions are opened explicitly by the use cases, not per request.
        "ATOMIC_REQUESTS": False,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "ledger": {"level": os.environ.get("LEDGER_LOG_LEVEL", "INFO")},
        # Unhandled exceptions (500) are logged here with their full traceback.
        "django.request": {"level": "ERROR"},
    },
}

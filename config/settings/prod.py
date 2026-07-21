"""Production settings with explicit secrets, hosts and reverse-proxy trust."""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import (
    ADMIN_CONTENT_SECURITY_POLICY as BASE_ADMIN_CONTENT_SECURITY_POLICY,
)
from .base import CONTENT_SECURITY_POLICY as BASE_CONTENT_SECURITY_POLICY
from .database import postgres_database_config


DEBUG = False

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY is required in production.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
if not ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS is required in production.")

DATABASES = {
    "default": postgres_database_config(os.environ.get("DJANGO_DATABASE_URL", ""))
}

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _environment_flag(variable, *, default="0"):
    value = os.environ.get(variable, default).strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ImproperlyConfigured(
        f"{variable} must be one of: 1, 0, true, false, yes, no, on or off."
    )


def _required_environment_value(variable):
    value = os.environ.get(variable, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{variable} is required in production.")
    return value


AGENDA_PLATFORM_LEGAL_DEMO = _environment_flag("AGENDA_PLATFORM_LEGAL_DEMO")
AGENDA_BACKUP_SCHEDULE_CONFIGURED = _environment_flag(
    "AGENDA_BACKUP_SCHEDULE_CONFIGURED"
)
AGENDA_TRANSACTIONAL_EMAIL_ENABLED = _environment_flag(
    "AGENDA_TRANSACTIONAL_EMAIL_ENABLED"
)
AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED = _environment_flag(
    "AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED"
)
AGENDA_MANUAL_DEMO_REFRESH_ENABLED = _environment_flag(
    "AGENDA_MANUAL_DEMO_REFRESH_ENABLED"
)
AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL = _environment_flag(
    "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL"
)

try:
    AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT = int(
        os.environ.get(
            "AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT",
            str(AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT),  # noqa: F405
        )
    )
    AGENDA_OPERATIONAL_EMAIL_DAILY_LIMIT = int(
        os.environ.get(
            "AGENDA_OPERATIONAL_EMAIL_DAILY_LIMIT",
            str(AGENDA_OPERATIONAL_EMAIL_DAILY_LIMIT),  # noqa: F405
        )
    )
    AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS = int(
        os.environ.get(
            "AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS",
            str(AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS),  # noqa: F405
        )
    )
except ValueError as exc:
    raise ImproperlyConfigured(
        "Operational email limits and the recommended demo refresh age must be integers."
    ) from exc
if (
    AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT < 1
    or AGENDA_OPERATIONAL_EMAIL_DAILY_LIMIT < AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT
):
    raise ImproperlyConfigured(
        "Operational email limits must be positive and the daily limit must be "
        "greater than or equal to the hourly limit."
    )
if AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS < 1:
    raise ImproperlyConfigured(
        "AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS must be greater than zero."
    )

_required_legal_settings = {
    variable: _required_environment_value(variable)
    for variable in (
        "AGENDA_PLATFORM_LEGAL_NAME",
        "AGENDA_PLATFORM_PRIVACY_EMAIL",
        "AGENDA_PLATFORM_WEBSITE",
    )
}

if AGENDA_PLATFORM_LEGAL_DEMO:
    AGENDA_DEMO_SUPERADMIN_PASSWORD = _required_environment_value(
        "AGENDA_DEMO_SUPERADMIN_PASSWORD"
    )
    if len(AGENDA_DEMO_SUPERADMIN_PASSWORD) < 16:
        raise ImproperlyConfigured(
            "AGENDA_DEMO_SUPERADMIN_PASSWORD must contain at least 16 characters."
        )
    for variable in ("AGENDA_PLATFORM_TAX_ID", "AGENDA_PLATFORM_LEGAL_ADDRESS"):
        if os.environ.get(variable, "").strip():
            raise ImproperlyConfigured(
                f"{variable} must be empty when AGENDA_PLATFORM_LEGAL_DEMO is enabled."
            )
    _required_legal_settings["AGENDA_PLATFORM_TAX_ID"] = ""
    _required_legal_settings["AGENDA_PLATFORM_LEGAL_ADDRESS"] = ""
else:
    AGENDA_DEMO_SUPERADMIN_PASSWORD = ""
    for variable in ("AGENDA_PLATFORM_TAX_ID", "AGENDA_PLATFORM_LEGAL_ADDRESS"):
        _required_legal_settings[variable] = _required_environment_value(variable)

AGENDA_PLATFORM_LEGAL_NAME = _required_legal_settings["AGENDA_PLATFORM_LEGAL_NAME"]
AGENDA_PLATFORM_TAX_ID = _required_legal_settings["AGENDA_PLATFORM_TAX_ID"]
AGENDA_PLATFORM_LEGAL_ADDRESS = _required_legal_settings[
    "AGENDA_PLATFORM_LEGAL_ADDRESS"
]
AGENDA_PLATFORM_PRIVACY_EMAIL = _required_legal_settings[
    "AGENDA_PLATFORM_PRIVACY_EMAIL"
]
AGENDA_PLATFORM_WEBSITE = _required_legal_settings["AGENDA_PLATFORM_WEBSITE"]

if AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL and not AGENDA_PLATFORM_LEGAL_DEMO:
    raise ImproperlyConfigured(
        "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL can only be enabled in academic demo mode."
    )

if AGENDA_MANUAL_DEMO_REFRESH_ENABLED and not AGENDA_PLATFORM_LEGAL_DEMO:
    raise ImproperlyConfigured(
        "AGENDA_MANUAL_DEMO_REFRESH_ENABLED can only be enabled in academic demo mode."
    )

if AGENDA_TRANSACTIONAL_EMAIL_ENABLED:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = _required_environment_value("EMAIL_HOST")
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    try:
        EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", str(EMAIL_TIMEOUT)))  # noqa: F405
        AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS = int(
            os.environ.get(
                "AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS",
                str(AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS),  # noqa: F405
            )
        )
    except ValueError as exc:
        raise ImproperlyConfigured(
            "EMAIL_TIMEOUT and AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS must be integers."
        ) from exc
    if EMAIL_TIMEOUT <= 0:
        raise ImproperlyConfigured("EMAIL_TIMEOUT must be greater than zero.")
    if AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS <= EMAIL_TIMEOUT:
        raise ImproperlyConfigured(
            "AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS must be greater than EMAIL_TIMEOUT."
        )
    EMAIL_HOST_USER = _required_environment_value("EMAIL_HOST_USER")
    EMAIL_HOST_PASSWORD = _required_environment_value("EMAIL_HOST_PASSWORD")
    DEFAULT_FROM_EMAIL = _required_environment_value("DEFAULT_FROM_EMAIL")
    EMAIL_USE_TLS = _environment_flag("EMAIL_USE_TLS", default="1")
    EMAIL_USE_SSL = _environment_flag("EMAIL_USE_SSL", default="0")
    if EMAIL_USE_TLS and EMAIL_USE_SSL:
        raise ImproperlyConfigured("EMAIL_USE_TLS and EMAIL_USE_SSL cannot both be enabled.")

# Defensa en profundidad para procesos de regeneración y entornos sin correo.
# El servicio de notificaciones aplica además su propia guarda antes de construir
# el mensaje, pero este backend evita que una llamada directa a django.core.mail
# pueda abrir una conexión SMTP por accidente.
if not AGENDA_TRANSACTIONAL_EMAIL_ENABLED or AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL:
    EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

CONTENT_SECURITY_POLICY = f"{BASE_CONTENT_SECURITY_POLICY}; upgrade-insecure-requests"
ADMIN_CONTENT_SECURITY_POLICY = (
    f"{BASE_ADMIN_CONTENT_SECURITY_POLICY}; upgrade-insecure-requests"
)

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 60
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

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

_required_legal_settings = {
    variable: _required_environment_value(variable)
    for variable in (
        "AGENDA_PLATFORM_LEGAL_NAME",
        "AGENDA_PLATFORM_PRIVACY_EMAIL",
        "AGENDA_PLATFORM_WEBSITE",
    )
}

if AGENDA_PLATFORM_LEGAL_DEMO:
    for variable in ("AGENDA_PLATFORM_TAX_ID", "AGENDA_PLATFORM_LEGAL_ADDRESS"):
        if os.environ.get(variable, "").strip():
            raise ImproperlyConfigured(
                f"{variable} must be empty when AGENDA_PLATFORM_LEGAL_DEMO is enabled."
            )
    _required_legal_settings["AGENDA_PLATFORM_TAX_ID"] = ""
    _required_legal_settings["AGENDA_PLATFORM_LEGAL_ADDRESS"] = ""
else:
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

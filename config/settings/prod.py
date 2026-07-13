"""Production settings.

Deployment is not active yet. These settings force secrets and hosts to come
from the environment when the deployment phase is explicitly opened.
"""

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

_required_legal_settings = {
    "AGENDA_PLATFORM_LEGAL_NAME": "",
    "AGENDA_PLATFORM_TAX_ID": "",
    "AGENDA_PLATFORM_LEGAL_ADDRESS": "",
    "AGENDA_PLATFORM_PRIVACY_EMAIL": "",
    "AGENDA_PLATFORM_WEBSITE": "",
}
for variable in _required_legal_settings:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{variable} is required in production.")
    _required_legal_settings[variable] = value

AGENDA_PLATFORM_LEGAL_NAME = _required_legal_settings["AGENDA_PLATFORM_LEGAL_NAME"]
AGENDA_PLATFORM_TAX_ID = _required_legal_settings["AGENDA_PLATFORM_TAX_ID"]
AGENDA_PLATFORM_LEGAL_ADDRESS = _required_legal_settings[
    "AGENDA_PLATFORM_LEGAL_ADDRESS"
]
AGENDA_PLATFORM_PRIVACY_EMAIL = _required_legal_settings[
    "AGENDA_PLATFORM_PRIVACY_EMAIL"
]
AGENDA_PLATFORM_WEBSITE = _required_legal_settings["AGENDA_PLATFORM_WEBSITE"]
AGENDA_PLATFORM_LEGAL_DEMO = False

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
SECURE_HSTS_SECONDS = 60
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False

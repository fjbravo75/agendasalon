"""Resolución segura de las credenciales usadas para regenerar la demo."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.core.demo_scenario import BUSINESS_MARI, BUSINESS_NORTE


MINIMUM_DEMO_PASSWORD_LENGTH = 16
DEMO_SUPERADMIN_PASSWORD_SETTING = "AGENDA_DEMO_SUPERADMIN_PASSWORD"
DEMO_PROFESSIONAL_PASSWORD_SETTINGS = {
    BUSINESS_MARI: "AGENDA_DEMO_MARI_PASSWORD",
    BUSINESS_NORTE: "AGENDA_DEMO_NORTE_PASSWORD",
}


def _required_demo_password(setting_name: str) -> str:
    value = getattr(settings, setting_name, "")
    if not isinstance(value, str) or not value.strip():
        raise ImproperlyConfigured(
            f"{setting_name} is required to seed or verify the demo."
        )
    value = value.strip()
    if len(value) < MINIMUM_DEMO_PASSWORD_LENGTH:
        raise ImproperlyConfigured(
            f"{setting_name} must contain at least {MINIMUM_DEMO_PASSWORD_LENGTH} characters."
        )
    return value


def load_demo_passwords() -> tuple[str, dict[str, str]]:
    """Devuelve las tres credenciales configuradas sin aplicar valores por defecto."""

    superadmin_password = _required_demo_password(DEMO_SUPERADMIN_PASSWORD_SETTING)
    professional_passwords = {
        business: _required_demo_password(setting_name)
        for business, setting_name in DEMO_PROFESSIONAL_PASSWORD_SETTINGS.items()
    }
    return superadmin_password, professional_passwords

"""Ajustes aislados para la suite SQLite."""

import secrets

from .dev import *  # noqa: F403


# Las credenciales de prueba nacen en cada proceso y nunca coinciden con la demo
# desplegada ni quedan fijadas en el repositorio.
AGENDA_DEMO_SUPERADMIN_PASSWORD = secrets.token_urlsafe(24)
AGENDA_DEMO_MARI_PASSWORD = secrets.token_urlsafe(24)
AGENDA_DEMO_NORTE_PASSWORD = secrets.token_urlsafe(24)

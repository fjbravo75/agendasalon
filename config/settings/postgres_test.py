"""Test settings for exercising the full suite against PostgreSQL."""

from .prod import *  # noqa: F403


# La suite funcional debe ejercer el flujo de correo completo igual que el
# entorno de desarrollo. El backend en memoria conserva esa semántica sin abrir
# conexiones SMTP ni entregar mensajes fuera del proceso de prueba.
AGENDA_TRANSACTIONAL_EMAIL_ENABLED = True
AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL = False
AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED = True
AGENDA_MANUAL_DEMO_REFRESH_ENABLED = True
AGENDA_PLATFORM_LEGAL_DEMO = True
AGENDA_DEMO_SUPERADMIN_PASSWORD = "AgendaSalonDemo1"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

from django.conf import settings


def transactional_email_delivery_enabled():
    """Indica si este proceso puede prometer una entrega externa real."""

    return bool(
        getattr(settings, "AGENDA_TRANSACTIONAL_EMAIL_ENABLED", False)
        and not getattr(settings, "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL", False)
    )


def operational_notifications_enabled():
    """Mantiene visible el centro aunque la salida de correo esté pausada."""

    return bool(getattr(settings, "AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED", False))


def operational_notification_delivery_enabled():
    return bool(
        operational_notifications_enabled()
        and transactional_email_delivery_enabled()
    )


def manual_demo_refresh_enabled():
    """Limita la acción destructiva al entorno académico que la declara."""

    return bool(
        getattr(settings, "AGENDA_PLATFORM_LEGAL_DEMO", False)
        and getattr(settings, "AGENDA_MANUAL_DEMO_REFRESH_ENABLED", False)
    )

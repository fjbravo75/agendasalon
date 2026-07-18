from django.conf import settings

from apps.core.features import (
    operational_notification_delivery_enabled,
    operational_notifications_enabled,
)


def feature_flags(_request):
    """Expone solo interruptores de producto no sensibles a las plantillas."""

    return {
        "agenda_operational_notifications_enabled": operational_notifications_enabled(),
        "agenda_operational_notification_delivery_enabled": (
            operational_notification_delivery_enabled()
        ),
        "agenda_manual_demo_refresh_enabled": getattr(
            settings,
            "AGENDA_MANUAL_DEMO_REFRESH_ENABLED",
            False,
        ),
    }

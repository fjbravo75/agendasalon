from django.urls import path

from apps.businesses.views import (
    superadmin_holiday_sync,
    superadmin_platform_contact_update,
    superadmin_platform_settings,
)


app_name = "platform_settings"

urlpatterns = [
    path("ajustes/", superadmin_platform_settings, name="superadmin_platform_settings"),
    path(
        "ajustes/contacto/",
        superadmin_platform_contact_update,
        name="superadmin_platform_contact_update",
    ),
    path("ajustes/festivos/sincronizar/", superadmin_holiday_sync, name="superadmin_holiday_sync"),
]

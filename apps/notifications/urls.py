from django.urls import path

from apps.notifications import views


app_name = "notifications"

urlpatterns = [
    path(
        "superadmin/avisos/",
        views.superadmin_notifications,
        name="superadmin_notifications",
    ),
    path(
        "superadmin/avisos/configuracion/",
        views.platform_notification_settings,
        name="platform_notification_settings",
    ),
    path(
        "superadmin/avisos/verificar/<str:token>/",
        views.platform_email_verify,
        name="platform_email_verify",
    ),
    path(
        "superadmin/avisos/prueba/",
        views.platform_email_test,
        name="platform_email_test",
    ),
    path(
        "profesional/ajustes/avisos/verificar/<str:token>/",
        views.business_email_verify,
        name="business_email_verify",
    ),
    path(
        "profesional/ajustes/avisos/prueba/",
        views.business_email_test,
        name="business_email_test",
    ),
]

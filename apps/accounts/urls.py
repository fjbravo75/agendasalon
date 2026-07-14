from django.urls import path
from django.views.generic import RedirectView

from apps.accounts.views import (
    AgendaSalonLoginView,
    account_email,
    account_security,
    logged_out,
    no_business,
    private_logout,
    professional_activate,
    professional_email_verify,
)


app_name = "accounts"

urlpatterns = [
    path("entrar/", AgendaSalonLoginView.as_view(), name="login"),
    path(
        "cuenta/entrar/",
        RedirectView.as_view(pattern_name="accounts:login", query_string=True),
        name="legacy_login",
    ),
    path("cuenta/seguridad/", account_security, name="security"),
    path("cuenta/correo/", account_email, name="email"),
    path(
        "activar-cuenta/<uidb64>/<str:token>/",
        professional_activate,
        name="professional_activate",
    ),
    path(
        "cuenta/verificar-correo/<uidb64>/<str:token>/",
        professional_email_verify,
        name="professional_email_verify",
    ),
    path("cuenta/salir/", private_logout, name="logout"),
    path("cuenta/desconectado/", logged_out, name="logged_out"),
    path("cuenta/sin-negocio/", no_business, name="no_business"),
]

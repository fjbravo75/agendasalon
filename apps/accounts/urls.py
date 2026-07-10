from django.contrib.auth.views import LogoutView
from django.urls import path

from apps.accounts.views import AgendaSalonLoginView, logged_out, no_business


app_name = "accounts"

urlpatterns = [
    path("entrar/", AgendaSalonLoginView.as_view(), name="login"),
    path("salir/", LogoutView.as_view(next_page="accounts:logged_out"), name="logout"),
    path("desconectado/", logged_out, name="logged_out"),
    path("sin-negocio/", no_business, name="no_business"),
]

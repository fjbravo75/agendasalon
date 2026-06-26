from django.contrib.auth.views import LogoutView
from django.urls import path

from apps.accounts.views import AgendaSalonLoginView, no_business


app_name = "accounts"

urlpatterns = [
    path("entrar/", AgendaSalonLoginView.as_view(), name="login"),
    path("salir/", LogoutView.as_view(), name="logout"),
    path("sin-negocio/", no_business, name="no_business"),
]

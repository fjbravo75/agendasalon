from django.urls import path

from apps.accounts.views import AgendaSalonLoginView, logged_out, no_business, private_logout


app_name = "accounts"

urlpatterns = [
    path("entrar/", AgendaSalonLoginView.as_view(), name="login"),
    path("salir/", private_logout, name="logout"),
    path("desconectado/", logged_out, name="logged_out"),
    path("sin-negocio/", no_business, name="no_business"),
]

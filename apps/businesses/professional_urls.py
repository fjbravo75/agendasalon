from django.urls import path

from apps.businesses.views import professional_settings


app_name = "business_settings"

urlpatterns = [
    path("ajustes/", professional_settings, name="professional_settings"),
]

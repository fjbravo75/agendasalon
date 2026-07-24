from django.urls import path

from apps.businesses.public_views import (
    business_signup_request,
    business_signup_request_success,
    platform_contact,
)


urlpatterns = [
    path("contacto/", platform_contact, name="platform_contact"),
    path("solicitar-alta/", business_signup_request, name="business_signup_request"),
    path(
        "solicitar-alta/recibida/",
        business_signup_request_success,
        name="business_signup_request_success",
    ),
]

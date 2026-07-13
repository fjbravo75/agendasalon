from django.urls import path

from apps.legal.views import (
    business_privacy,
    legal_index,
    platform_document,
    professional_center,
    professional_onboarding,
    professional_rights_request_update,
)


app_name = "legal"

urlpatterns = [
    path("", legal_index, name="legal_index"),
    path("documentos/<slug:slug>/", platform_document, name="platform_document"),
    path("negocios/<slug:slug>/privacidad/", business_privacy, name="business_privacy"),
    path("profesional/alta/", professional_onboarding, name="professional_onboarding"),
    path("profesional/", professional_center, name="professional_center"),
    path(
        "profesional/solicitudes/<int:request_id>/",
        professional_rights_request_update,
        name="professional_rights_request_update",
    ),
]

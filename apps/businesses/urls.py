from django.urls import path

from apps.businesses.views import (
    superadmin_business_activity,
    superadmin_business_create,
    superadmin_business_detail,
    superadmin_business_edit,
    superadmin_business_list,
    superadmin_business_legal_evidence,
    superadmin_business_toggle,
    superadmin_signup_request_detail,
    superadmin_signup_request_list,
    superadmin_membership_toggle,
    superadmin_professional_activation_resend,
    superadmin_professional_create,
    superadmin_public_booking_toggle,
)


app_name = "businesses"

urlpatterns = [
    path("", superadmin_business_list, name="superadmin_business_list"),
    path("nuevo/", superadmin_business_create, name="superadmin_business_create"),
    path(
        "solicitudes/",
        superadmin_signup_request_list,
        name="superadmin_signup_request_list",
    ),
    path(
        "solicitudes/<int:request_id>/",
        superadmin_signup_request_detail,
        name="superadmin_signup_request_detail",
    ),
    path("<int:business_id>/", superadmin_business_detail, name="superadmin_business_detail"),
    path(
        "<int:business_id>/actividad/",
        superadmin_business_activity,
        name="superadmin_business_activity",
    ),
    path(
        "<int:business_id>/privacidad/",
        superadmin_business_legal_evidence,
        name="superadmin_business_legal_evidence",
    ),
    path("<int:business_id>/editar/", superadmin_business_edit, name="superadmin_business_edit"),
    path("<int:business_id>/estado/", superadmin_business_toggle, name="superadmin_business_toggle"),
    path(
        "<int:business_id>/reserva-publica/",
        superadmin_public_booking_toggle,
        name="superadmin_public_booking_toggle",
    ),
    path(
        "<int:business_id>/profesionales/nuevo/",
        superadmin_professional_create,
        name="superadmin_professional_create",
    ),
    path(
        "<int:business_id>/profesionales/<int:membership_id>/estado/",
        superadmin_membership_toggle,
        name="superadmin_membership_toggle",
    ),
    path(
        "<int:business_id>/profesionales/<int:membership_id>/reenviar-activacion/",
        superadmin_professional_activation_resend,
        name="superadmin_professional_activation_resend",
    ),
]

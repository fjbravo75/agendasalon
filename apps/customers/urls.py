from django.urls import path

from apps.customers.views import (
    client_access,
    client_logout,
    client_register,
    professional_client_access_toggle,
    professional_client_detail,
    professional_client_edit,
    professional_client_list,
    professional_client_toggle,
    professional_contact_create,
    professional_contact_edit,
    professional_contact_toggle,
)


app_name = "customers"

urlpatterns = [
    path("profesional/", professional_client_list, name="professional_client_list"),
    path("profesional/<int:client_id>/", professional_client_detail, name="professional_client_detail"),
    path("profesional/<int:client_id>/editar/", professional_client_edit, name="professional_client_edit"),
    path("profesional/<int:client_id>/estado/", professional_client_toggle, name="professional_client_toggle"),
    path(
        "profesional/<int:client_id>/cuenta-online/estado/",
        professional_client_access_toggle,
        name="professional_client_access_toggle",
    ),
    path(
        "profesional/<int:client_id>/contactos/nuevo/",
        professional_contact_create,
        name="professional_contact_create",
    ),
    path(
        "profesional/<int:client_id>/contactos/<int:contact_id>/editar/",
        professional_contact_edit,
        name="professional_contact_edit",
    ),
    path(
        "profesional/<int:client_id>/contactos/<int:contact_id>/estado/",
        professional_contact_toggle,
        name="professional_contact_toggle",
    ),
    path("<slug:slug>/entrar/", client_access, name="client_access"),
    path("<slug:slug>/registro/", client_register, name="client_register"),
    path("<slug:slug>/salir/", client_logout, name="client_logout"),
]

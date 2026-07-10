from django.urls import path

from apps.booking.views import (
    appointment_assistant,
    professional_appointment_cancel,
    professional_appointment_complete,
    professional_appointment_detail,
    professional_availability_edit,
    professional_availability_toggle,
    professional_closure_edit,
    professional_closure_toggle,
    professional_schedule,
    professional_service_edit,
    professional_service_list,
    professional_service_toggle,
    professional_work_line_edit,
    professional_work_line_toggle,
)


app_name = "booking"

urlpatterns = [
    path("citas/nueva/", appointment_assistant, name="appointment_assistant"),
    path("citas/<int:appointment_id>/", professional_appointment_detail, name="professional_appointment_detail"),
    path("citas/<int:appointment_id>/cancelar/", professional_appointment_cancel, name="professional_appointment_cancel"),
    path("citas/<int:appointment_id>/completar/", professional_appointment_complete, name="professional_appointment_complete"),
    path("servicios/", professional_service_list, name="professional_service_list"),
    path("servicios/<int:service_id>/", professional_service_edit, name="professional_service_edit"),
    path("servicios/<int:service_id>/estado/", professional_service_toggle, name="professional_service_toggle"),
    path("horarios/", professional_schedule, name="professional_schedule"),
    path("horarios/tramos/<int:rule_id>/", professional_availability_edit, name="professional_availability_edit"),
    path(
        "horarios/tramos/<int:rule_id>/estado/",
        professional_availability_toggle,
        name="professional_availability_toggle",
    ),
    path("horarios/lineas/<int:line_id>/", professional_work_line_edit, name="professional_work_line_edit"),
    path(
        "horarios/lineas/<int:line_id>/estado/",
        professional_work_line_toggle,
        name="professional_work_line_toggle",
    ),
    path("horarios/cierres/<int:closure_id>/", professional_closure_edit, name="professional_closure_edit"),
    path(
        "horarios/cierres/<int:closure_id>/estado/",
        professional_closure_toggle,
        name="professional_closure_toggle",
    ),
]

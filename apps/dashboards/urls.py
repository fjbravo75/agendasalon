from django.urls import path

from apps.dashboards.api import superadmin_dashboard_data
from apps.dashboards.views import professional_home, superadmin_continuity, superadmin_home


app_name = "dashboards"

urlpatterns = [
    path("profesional/", professional_home, name="professional_home"),
    path("superadmin/dashboard/", superadmin_home, name="superadmin_home"),
    path(
        "superadmin/continuidad/",
        superadmin_continuity,
        name="superadmin_continuity",
    ),
    path(
        "superadmin/dashboard/datos/",
        superadmin_dashboard_data,
        name="superadmin_dashboard_data",
    ),
]

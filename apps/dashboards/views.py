from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from apps.booking.models import Appointment
from apps.businesses.models import Business
from apps.businesses.services import get_primary_business_for_user
from apps.customers.models import BusinessClient


@login_required
def professional_home(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    context = {
        "business": business,
        "is_operational": business.is_operational_for_agenda(),
        "services_count": business.services.filter(is_active=True).count(),
        "work_lines_count": business.work_lines.filter(is_active=True).count(),
        "appointments_count": business.appointments.count(),
    }
    return render(request, "professional/home.html", context)


@login_required
def superadmin_home(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("No tienes permiso para acceder a este panel.")

    User = get_user_model()
    context = {
        "total_businesses": Business.objects.count(),
        "active_businesses": Business.objects.filter(is_active=True).count(),
        "inactive_businesses": Business.objects.filter(is_active=False).count(),
        "professionals_count": User.objects.filter(
            business_memberships__is_active=True,
            business_memberships__business__is_active=True,
        )
        .distinct()
        .count(),
        "clients_count": BusinessClient.objects.count(),
        "appointments_count": Appointment.objects.count(),
    }
    return render(request, "superadmin/home.html", context)

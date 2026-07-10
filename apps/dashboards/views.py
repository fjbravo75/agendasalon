from datetime import datetime, time, timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment
from apps.booking.slot_engine import STATUS_CLOSED, get_day_availability, suggest_next_slots
from apps.businesses.services import get_primary_business_for_user


SLOT_REASON_LABELS = {
    "rellena_hueco_exacto": "Encaja sin dejar huecos sueltos",
    "compacta_agenda": "Ayuda a concentrar la jornada",
    "evita_restos_pequenos": "Revisa si deja margen corto",
    "hueco_valido": "Disponible para ofrecer",
}


def _day_bounds(target_date):
    current_timezone = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(target_date, time.min), current_timezone)
    return day_start, day_start + timedelta(days=1)


def _decorate_appointment(appointment):
    appointment.local_starts_at = timezone.localtime(appointment.starts_at)
    appointment.local_ends_at = timezone.localtime(appointment.ends_at)
    if appointment.is_pending_closure():
        appointment.operational_status_label = "Pendiente de cierre"
        appointment.operational_status_css = "pending-closure"
    else:
        appointment.operational_status_label = appointment.get_status_display()
        appointment.operational_status_css = appointment.status
    return appointment


def _slot_reason_label(reason):
    return SLOT_REASON_LABELS.get(reason, "Disponible para ofrecer")


@login_required
def professional_home(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    today = timezone.localdate()
    now = timezone.now()
    day_start, day_end = _day_bounds(today)
    active_services = business.services.filter(is_active=True).order_by("display_order", "name", "pk")
    active_work_lines = list(
        business.work_lines.filter(is_active=True).order_by("display_order", "line_number", "pk")
    )
    today_appointments = list(
        business.appointments.select_related("business_client", "work_line")
        .filter(starts_at__gte=day_start, starts_at__lt=day_end)
        .order_by("starts_at", "work_line__display_order", "work_line__line_number", "pk")
    )
    for appointment in today_appointments:
        _decorate_appointment(appointment)

    appointments_by_line = {line.id: [] for line in active_work_lines}
    today_confirmed_count = 0
    today_minutes = 0
    for appointment in today_appointments:
        appointments_by_line.setdefault(appointment.work_line_id, []).append(appointment)
        if appointment.status == Appointment.Status.CONFIRMED:
            today_confirmed_count += 1
            today_minutes += appointment.total_duration_minutes

    line_boards = []
    for line in active_work_lines:
        line_appointments = appointments_by_line.get(line.id, [])
        confirmed_appointments = [
            appointment
            for appointment in line_appointments
            if appointment.status == Appointment.Status.CONFIRMED
        ]
        confirmed_minutes = sum(appointment.total_duration_minutes for appointment in confirmed_appointments)
        if line_appointments:
            status_label = (
                f"{len(line_appointments)} cita"
                if len(line_appointments) == 1
                else f"{len(line_appointments)} citas"
            )
            status_text = f"{confirmed_minutes} min ocupados en esta línea."
        else:
            status_label = "Libre hoy"
            status_text = "Disponible para nuevas citas dentro del horario activo."
        line_boards.append(
            {
                "line": line,
                "appointments": line_appointments,
                "confirmed_count": len(confirmed_appointments),
                "confirmed_minutes": confirmed_minutes,
                "status_label": status_label,
                "status_text": status_text,
            }
        )

    next_appointment = (
        business.appointments.select_related("business_client", "work_line")
        .filter(status=Appointment.Status.CONFIRMED, starts_at__gte=now)
        .order_by("starts_at", "pk")
        .first()
    )
    if next_appointment is not None:
        _decorate_appointment(next_appointment)

    overdue_appointments_queryset = (
        business.appointments.select_related("business_client", "work_line")
        .filter(status=Appointment.Status.CONFIRMED, ends_at__lte=now)
        .order_by("ends_at", "pk")
    )
    overdue_appointments = list(overdue_appointments_queryset[:5])
    for appointment in overdue_appointments:
        _decorate_appointment(appointment)
    overdue_appointments_count = overdue_appointments_queryset.count()

    default_service = active_services.first()
    day_availability = None
    recommended_slots = []
    if default_service is not None:
        day_availability = get_day_availability(
            business=business,
            target_date=today,
            duration_minutes=default_service.duration_minutes,
            now=now,
        )
        recommended_slots = list(
            suggest_next_slots(
                business=business,
                start_date=today,
                duration_minutes=default_service.duration_minutes,
                now=now,
                limit=3,
            )
        )
    recommended_slot_cards = [
        {
            "slot": slot,
            "reason_label": _slot_reason_label(slot.reason),
        }
        for slot in recommended_slots
    ]

    availability_rules = list(
        business.availability_rules.filter(weekday=today.weekday(), is_active=True).order_by("start_time", "pk")
    )
    availability_rules_count = business.availability_rules.filter(is_active=True).count()
    today_closures = business.closures.filter(is_active=True, date_from__lte=today, date_to__gte=today)
    has_full_closure = today_closures.filter(
        work_line__isnull=True,
        start_time__isnull=True,
        end_time__isnull=True,
    ).exists()

    day_is_closed = bool(day_availability and day_availability.status == STATUS_CLOSED)
    day_reason = day_availability.reason if day_availability else ""
    if day_reason == "festivo_nacional":
        day_status_label = "Festivo nacional hoy"
        day_status_text = "La jornada está cerrada por festivo nacional."
    elif day_reason == "negocio_inactivo":
        day_status_label = "Negocio pausado"
        day_status_text = "El negocio no admite nuevas citas mientras siga pausado."
    elif has_full_closure or day_reason == "cierre_negocio":
        day_status_label = "Cierre completo hoy"
        day_status_text = "Hay un cierre completo registrado para esta jornada."
    elif day_reason == "sin_lineas_activas":
        day_status_label = "Sin capacidad activa"
        day_status_text = "Activa al menos una línea para poder ofrecer citas."
    elif availability_rules:
        day_status_label = "Horario activo hoy"
        day_status_text = "El horario de hoy está disponible para organizar citas."
    else:
        day_status_label = "Sin horario para hoy"
        day_status_text = "No hay horario activo para esta jornada."

    if day_is_closed:
        for board in line_boards:
            board["status_label"] = "No disponible hoy"
            board["status_text"] = day_status_text

    services_count = active_services.count()
    work_lines_count = len(active_work_lines)
    clients_count = business.clients.filter(is_active=True).count()
    client_accesses_count = business.client_accesses.filter(is_active=True).count()
    is_operational = business.is_operational_for_agenda()

    if day_is_closed and today_confirmed_count == 0:
        today_summary_label = "Jornada cerrada"
        today_summary_text = day_status_text
    elif today_confirmed_count == 0:
        today_summary_label = "0 citas"
        today_summary_text = "La agenda está libre por ahora."
    elif today_confirmed_count == 1:
        today_summary_label = "1 cita"
        today_summary_text = f"{today_minutes} min confirmados para hoy."
    else:
        today_summary_label = f"{today_confirmed_count} citas"
        today_summary_text = f"{today_minutes} min confirmados para hoy."

    if overdue_appointments_count:
        salon_status_label = "Con tareas"
        salon_status_text = (
            f"{overdue_appointments_count} cita pasada pendiente de cerrar."
            if overdue_appointments_count == 1
            else f"{overdue_appointments_count} citas pasadas pendientes de cerrar."
        )
    elif day_reason == "festivo_nacional":
        salon_status_label = "Cerrado hoy"
        salon_status_text = "La jornada está cerrada por festivo nacional."
    elif has_full_closure or day_reason == "cierre_negocio":
        salon_status_label = "Cerrado hoy"
        salon_status_text = "Hay un cierre completo registrado para la jornada."
    elif not availability_rules:
        salon_status_label = "Revisar"
        salon_status_text = "No hay horario activo para trabajar hoy."
    elif is_operational:
        salon_status_label = "Listo"
        salon_status_text = "Servicios, líneas y horario activos para trabajar."
    else:
        salon_status_label = "Revisar"
        salon_status_text = "Falta completar algún dato antes de agendar con seguridad."

    context = {
        "business": business,
        "is_operational": is_operational,
        "services_count": services_count,
        "work_lines_count": work_lines_count,
        "appointments_count": business.appointments.count(),
        "today": today,
        "today_confirmed_count": today_confirmed_count,
        "today_total_count": len(today_appointments),
        "today_minutes": today_minutes,
        "today_summary_label": today_summary_label,
        "today_summary_text": today_summary_text,
        "line_boards": line_boards,
        "next_appointment": next_appointment,
        "overdue_appointments": overdue_appointments,
        "overdue_appointments_count": overdue_appointments_count,
        "overdue_appointments_hidden_count": max(overdue_appointments_count - len(overdue_appointments), 0),
        "default_service": default_service,
        "recommended_slots": recommended_slots,
        "recommended_slot_cards": recommended_slot_cards,
        "availability_rules": availability_rules,
        "day_status_label": day_status_label,
        "day_status_text": day_status_text,
        "day_is_closed": day_is_closed,
        "salon_status_label": salon_status_label,
        "salon_status_text": salon_status_text,
        "today_closures_count": today_closures.count() + (1 if day_reason == "festivo_nacional" else 0),
        "clients_count": clients_count,
        "client_accesses_count": client_accesses_count,
        "setup_items": [
            {"label": "Servicios para reservar", "value": services_count, "ready": services_count > 0},
            {"label": "Líneas activas", "value": work_lines_count, "ready": work_lines_count > 0},
            {"label": "Horario cargado", "value": availability_rules_count, "ready": availability_rules_count > 0},
        ],
    }
    return render(request, "professional/home.html", context)


@login_required
def superadmin_home(request):
    if not request.user.is_superuser:
        return HttpResponseForbidden("No tienes permiso para acceder a este panel.")
    return render(
        request,
        "superadmin/home.html",
        {
            "dashboard_config": {
                "dataEndpoint": reverse("dashboards:superadmin_dashboard_data"),
                "businessListUrl": reverse("businesses:superadmin_business_list"),
                "businessCreateUrl": reverse("businesses:superadmin_business_create"),
            }
        },
    )

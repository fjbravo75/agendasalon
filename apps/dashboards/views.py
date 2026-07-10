from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.booking.models import Appointment
from apps.booking.slot_engine import suggest_next_slots
from apps.businesses.models import Business
from apps.businesses.services import get_primary_business_for_user
from apps.customers.models import BusinessClient


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

    overdue_appointments = list(
        business.appointments.select_related("business_client", "work_line")
        .filter(status=Appointment.Status.CONFIRMED, ends_at__lte=now)
        .order_by("ends_at", "pk")[:4]
    )
    for appointment in overdue_appointments:
        _decorate_appointment(appointment)
    overdue_appointments_count = business.appointments.filter(
        status=Appointment.Status.CONFIRMED,
        ends_at__lte=now,
    ).count()

    default_service = active_services.first()
    recommended_slots = []
    if default_service is not None:
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

    if has_full_closure:
        day_status_label = "Cierre completo hoy"
    elif availability_rules:
        day_status_label = "Horario activo hoy"
    else:
        day_status_label = "Sin horario para hoy"

    services_count = active_services.count()
    work_lines_count = len(active_work_lines)
    clients_count = business.clients.filter(is_active=True).count()
    client_accesses_count = business.client_accesses.filter(is_active=True).count()
    is_operational = business.is_operational_for_agenda()

    if today_confirmed_count == 0:
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
    elif has_full_closure:
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
        "default_service": default_service,
        "recommended_slots": recommended_slots,
        "recommended_slot_cards": recommended_slot_cards,
        "availability_rules": availability_rules,
        "day_status_label": day_status_label,
        "salon_status_label": salon_status_label,
        "salon_status_text": salon_status_text,
        "today_closures_count": today_closures.count(),
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

    User = get_user_model()
    now = timezone.now()
    businesses = list(
        Business.objects.annotate(
            active_services_count=Count(
                "services",
                filter=Q(services__is_active=True),
                distinct=True,
            ),
            active_lines_count=Count(
                "work_lines",
                filter=Q(work_lines__is_active=True),
                distinct=True,
            ),
            active_rules_count=Count(
                "availability_rules",
                filter=Q(availability_rules__is_active=True),
                distinct=True,
            ),
            clients_total=Count("clients", filter=Q(clients__is_active=True), distinct=True),
            appointments_total=Count("appointments", distinct=True),
            professionals_total=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            ),
        ).order_by("commercial_name", "pk")
    )
    operational_businesses_count = 0
    attention_businesses_count = 0
    for business in businesses:
        missing_setup = []
        if not business.active_services_count:
            missing_setup.append("servicios")
        if not business.active_lines_count:
            missing_setup.append("líneas")
        if not business.active_rules_count:
            missing_setup.append("horario")

        if not business.is_active:
            business.health_label = "Inactivo"
            business.health_tone = "quiet"
            business.health_detail = "El negocio no admite nuevas reservas."
        elif missing_setup:
            business.health_label = "Completar configuración"
            business.health_tone = "warning"
            business.health_detail = f"Falta: {', '.join(missing_setup)}."
            attention_businesses_count += 1
        else:
            business.health_label = "Operativo"
            business.health_tone = "ready"
            business.health_detail = "Puede gestionar citas y reservas online."
            operational_businesses_count += 1

    recent_appointments = list(
        Appointment.objects.select_related("business", "business_client", "work_line")
        .order_by("-created_at", "-pk")[:6]
    )
    for appointment in recent_appointments:
        _decorate_appointment(appointment)

    status_counts = {
        item["status"]: item["total"]
        for item in Appointment.objects.values("status").annotate(total=Count("id"))
    }
    channel_counts = {
        item["manual_channel"]: item["total"]
        for item in Appointment.objects.values("manual_channel").annotate(total=Count("id"))
    }
    overdue_appointments_count = Appointment.objects.filter(
        status=Appointment.Status.CONFIRMED,
        ends_at__lte=now,
    ).count()

    context = {
        "businesses": businesses,
        "total_businesses": Business.objects.count(),
        "active_businesses": Business.objects.filter(is_active=True).count(),
        "inactive_businesses": Business.objects.filter(is_active=False).count(),
        "operational_businesses_count": operational_businesses_count,
        "attention_businesses_count": attention_businesses_count,
        "professionals_count": User.objects.filter(
            business_memberships__is_active=True,
            business_memberships__business__is_active=True,
        )
        .distinct()
        .count(),
        "clients_count": BusinessClient.objects.count(),
        "appointments_count": Appointment.objects.count(),
        "overdue_appointments_count": overdue_appointments_count,
        "recent_appointments": recent_appointments,
        "status_summary": [
            {"label": "Confirmadas", "value": status_counts.get(Appointment.Status.CONFIRMED, 0)},
            {"label": "Completadas", "value": status_counts.get(Appointment.Status.COMPLETED, 0)},
            {"label": "Canceladas", "value": status_counts.get(Appointment.Status.CANCELLED, 0)},
        ],
        "channel_summary": [
            {"label": "Reserva online", "value": channel_counts.get(Appointment.ManualChannel.PUBLIC_WEB, 0)},
            {"label": "Teléfono", "value": channel_counts.get(Appointment.ManualChannel.PHONE, 0)},
            {"label": "Mostrador", "value": channel_counts.get(Appointment.ManualChannel.FRONT_DESK, 0)},
            {"label": "WhatsApp", "value": channel_counts.get(Appointment.ManualChannel.WHATSAPP, 0)},
            {"label": "Email", "value": channel_counts.get(Appointment.ManualChannel.EMAIL, 0)},
        ],
    }
    return render(request, "superadmin/home.html", context)

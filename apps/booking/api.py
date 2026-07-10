from datetime import date, datetime, time, timedelta

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_cookie

from apps.booking.models import Appointment
from apps.booking.slot_engine import (
    get_day_availability,
    get_month_availability,
    suggest_next_slots,
)
from apps.businesses.services import get_primary_business_for_user
from apps.holidays.models import OfficialHoliday


AGENDA_API_VERSION = "1.0"
DEFAULT_DURATION_MINUTES = 60
MAX_DURATION_MINUTES = 12 * 60


@login_required
@require_GET
@never_cache
@vary_on_cookie
def professional_agenda_day_data(request):
    business, error_response = _professional_business_or_error(request)
    if error_response is not None:
        return error_response

    target_date, error_response = _date_query_parameter(request, "date")
    if error_response is not None:
        return error_response

    duration_minutes, error_response = _duration_query_parameter(request, business)
    if error_response is not None:
        return error_response

    now = timezone.now()
    day_start, day_end = _day_bounds(target_date)
    day_availability = get_day_availability(
        business=business,
        target_date=target_date,
        duration_minutes=duration_minutes,
        now=now,
    )
    suggestions = suggest_next_slots(
        business=business,
        start_date=target_date,
        duration_minutes=duration_minutes,
        now=now,
        limit=3,
    )
    appointments = list(
        business.appointments.select_related("business_client", "work_line")
        .filter(starts_at__lt=day_end, ends_at__gt=day_start)
        .order_by("starts_at", "work_line__display_order", "work_line__line_number", "pk")
    )
    closures = list(
        business.closures.select_related("work_line")
        .filter(
            is_active=True,
            date_from__lte=target_date,
            date_to__gte=target_date,
        )
        .order_by("start_time", "work_line__display_order", "work_line__line_number", "pk")
    )
    holidays = _national_holidays_for_business(business, target_date)
    work_lines = _agenda_work_lines(business, appointments)
    ranked_slots = day_availability.slots

    return JsonResponse(
        {
            "schema_version": AGENDA_API_VERSION,
            "business": {
                "id": business.id,
                "name": business.commercial_name,
                "slug": business.slug,
            },
            "query": {
                "date": target_date.isoformat(),
                "duration_minutes": duration_minutes,
            },
            "calendar": {
                "timezone": timezone.get_current_timezone_name(),
                "slot_interval_minutes": _slot_interval_minutes(business),
                "status": day_availability.status,
                "reason": day_availability.reason or None,
                "calculated_from": _datetime_value(day_availability.calculated_from),
            },
            "holidays": [_holiday_payload(holiday) for holiday in holidays],
            "closures": [_closure_payload(closure) for closure in closures],
            "work_lines": [
                _work_line_payload(
                    line,
                    appointments=appointments,
                    slots=day_availability.slots_by_line.get(line.id, ()),
                    now=now,
                )
                for line in work_lines
            ],
            "recommended_slot": _slot_payload(ranked_slots[0]) if ranked_slots else None,
            "suggestions": [_slot_payload(slot) for slot in suggestions],
        }
    )


@login_required
@require_GET
@never_cache
@vary_on_cookie
def professional_agenda_month_data(request):
    business, error_response = _professional_business_or_error(request)
    if error_response is not None:
        return error_response

    today = timezone.localdate()
    year, error_response = _integer_query_parameter(
        request,
        "year",
        default=today.year,
        minimum=2000,
        maximum=2100,
    )
    if error_response is not None:
        return error_response
    month, error_response = _integer_query_parameter(
        request,
        "month",
        default=today.month,
        minimum=1,
        maximum=12,
    )
    if error_response is not None:
        return error_response
    duration_minutes, error_response = _duration_query_parameter(request, business)
    if error_response is not None:
        return error_response

    month_availability = get_month_availability(
        business=business,
        year=year,
        month=month,
        duration_minutes=duration_minutes,
        now=timezone.now(),
    )

    return JsonResponse(
        {
            "schema_version": AGENDA_API_VERSION,
            "business": {
                "id": business.id,
                "name": business.commercial_name,
                "slug": business.slug,
            },
            "query": {
                "year": year,
                "month": month,
                "duration_minutes": duration_minutes,
            },
            "calendar": {
                "timezone": timezone.get_current_timezone_name(),
                "slot_interval_minutes": _slot_interval_minutes(business),
            },
            "days": [
                {
                    "date": day.date.isoformat(),
                    "status": day.status,
                    "reason": day.reason or None,
                    "first_slot": _slot_payload(day.first_slot) if day.first_slot else None,
                }
                for day in month_availability.days
            ],
        }
    )


def _professional_business_or_error(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return None, _error_response(
            "professional_business_required",
            "Se necesita un acceso profesional activo para consultar esta agenda.",
            status=403,
        )
    return business, None


def _date_query_parameter(request, name):
    raw_value = request.GET.get(name)
    if not raw_value:
        return timezone.localdate(), None
    try:
        return date.fromisoformat(raw_value), None
    except ValueError:
        return None, _error_response(
            "invalid_query",
            f"El parámetro «{name}» debe usar el formato AAAA-MM-DD.",
        )


def _duration_query_parameter(request, business):
    duration_minutes, error_response = _integer_query_parameter(
        request,
        "duration",
        default=DEFAULT_DURATION_MINUTES,
        minimum=15,
        maximum=MAX_DURATION_MINUTES,
    )
    if error_response is not None:
        return None, error_response
    if duration_minutes % 15 != 0:
        return None, _error_response(
            "invalid_query",
            "El parámetro «duration» debe usar tramos de 15 minutos.",
        )
    slot_interval_minutes = _slot_interval_minutes(business)
    if duration_minutes % slot_interval_minutes != 0:
        return None, _error_response(
            "invalid_query",
            (
                "El parámetro «duration» debe ser compatible con el intervalo "
                f"de agenda de {slot_interval_minutes} minutos."
            ),
        )
    return duration_minutes, None


def _integer_query_parameter(request, name, *, default, minimum, maximum):
    raw_value = request.GET.get(name)
    if raw_value in (None, ""):
        return default, None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None, _error_response(
            "invalid_query",
            f"El parámetro «{name}» debe ser un número entero.",
        )
    if not minimum <= value <= maximum:
        return None, _error_response(
            "invalid_query",
            f"El parámetro «{name}» debe estar entre {minimum} y {maximum}.",
        )
    return value, None


def _agenda_work_lines(business, appointments):
    appointment_line_ids = {appointment.work_line_id for appointment in appointments}
    return list(
        business.work_lines.filter(Q(is_active=True) | Q(pk__in=appointment_line_ids))
        .distinct()
        .order_by("display_order", "line_number", "pk")
    )


def _work_line_payload(line, *, appointments, slots, now):
    line_appointments = [
        appointment
        for appointment in appointments
        if appointment.work_line_id == line.id
    ]
    return {
        "id": line.id,
        "number": line.line_number,
        "name": line.name or f"Línea {line.line_number}",
        "is_active": line.is_active,
        "appointments": [
            _appointment_payload(appointment, now=now)
            for appointment in line_appointments
        ],
        "available_slots": [_slot_payload(slot) for slot in slots],
    }


def _appointment_payload(appointment, *, now):
    return {
        "id": appointment.id,
        "client": {
            "id": appointment.business_client_id,
            "name": appointment.business_client.full_name,
        },
        "starts_at": _datetime_value(appointment.starts_at),
        "ends_at": _datetime_value(appointment.ends_at),
        "duration_minutes": appointment.total_duration_minutes,
        "status": appointment.status,
        "status_label": (
            "Pendiente de cierre"
            if appointment.is_pending_closure(at=now)
            else appointment.get_status_display()
        ),
        "channel": appointment.manual_channel,
        "channel_label": appointment.get_manual_channel_display(),
        "service_summary": appointment.service_summary_snapshot,
    }


def _closure_payload(closure):
    return {
        "id": closure.id,
        "type": closure.closure_type,
        "type_label": closure.get_closure_type_display(),
        "work_line_id": closure.work_line_id,
        "date_from": closure.date_from.isoformat(),
        "date_to": closure.date_to.isoformat(),
        "start_time": _time_value(closure.start_time),
        "end_time": _time_value(closure.end_time),
        "reason": closure.internal_reason,
    }


def _holiday_payload(holiday):
    if holiday is None:
        return None
    return {
        "date": holiday.date.isoformat(),
        "name": holiday.name,
        "scope": holiday.scope,
        "scope_label": holiday.get_scope_display(),
    }


def _slot_payload(slot):
    return {
        "work_line_id": slot.work_line_id,
        "work_line_name": slot.work_line_name,
        "work_line_number": slot.work_line_number,
        "starts_at": _datetime_value(slot.starts_at),
        "ends_at": _datetime_value(slot.ends_at),
        "duration_minutes": slot.duration_minutes,
        "score": slot.score,
        "reason": slot.reason,
    }


def _national_holidays_for_business(business, target_date):
    try:
        applies_national_holidays = business.calendar_settings.apply_national_holidays
    except ObjectDoesNotExist:
        applies_national_holidays = True
    if not applies_national_holidays:
        return []
    return list(
        OfficialHoliday.objects.filter(
            date=target_date,
            scope=OfficialHoliday.Scope.NATIONAL,
        )
        .order_by("name", "pk")
    )


def _slot_interval_minutes(business):
    try:
        return business.calendar_settings.slot_interval_minutes
    except ObjectDoesNotExist:
        return 15


def _day_bounds(target_date):
    current_timezone = timezone.get_current_timezone()
    starts_at = timezone.make_aware(datetime.combine(target_date, time.min), current_timezone)
    return starts_at, starts_at + timedelta(days=1)


def _datetime_value(value):
    if value is None:
        return None
    return timezone.localtime(value).isoformat()


def _time_value(value):
    return value.isoformat(timespec="minutes") if value else None


def _error_response(code, message, *, status=400):
    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": message,
            }
        },
        status=status,
    )

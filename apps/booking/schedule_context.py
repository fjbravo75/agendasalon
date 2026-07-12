from collections import defaultdict

from django.db.models import Count, Q
from django.utils import timezone

from apps.booking.forms import CLOSURE_TYPE_CHOICES
from apps.booking.models import (
    Appointment,
    AvailabilityRule,
    BusinessClosure,
    WorkLine,
)
from apps.holidays.models import HolidaySyncRun, OfficialHoliday


WEEKDAY_LABELS = (
    (0, "Lunes"),
    (1, "Martes"),
    (2, "Miércoles"),
    (3, "Jueves"),
    (4, "Viernes"),
    (5, "Sábado"),
    (6, "Domingo"),
)


def weekday_label(weekday):
    return dict(WEEKDAY_LABELS).get(weekday, "")


def closure_type_label(closure_type):
    return dict(CLOSURE_TYPE_CHOICES).get(closure_type, "")


def build_schedule_management_context(
    business,
    availability_form,
    closure_form,
    work_line_form,
    editing_availability,
    editing_closure,
    editing_work_line,
):
    today = timezone.localdate()
    now = timezone.now()
    availability_rules = tuple(
        AvailabilityRule.objects.filter(business=business).order_by("weekday", "start_time", "pk")
    )
    rules_by_weekday = defaultdict(list)
    for rule in availability_rules:
        rules_by_weekday[rule.weekday].append(rule)

    weekday_sections = tuple(
        {
            "weekday": weekday,
            "label": label,
            "rules": tuple(rules_by_weekday.get(weekday, [])),
            "active_count": sum(1 for rule in rules_by_weekday.get(weekday, []) if rule.is_active),
        }
        for weekday, label in WEEKDAY_LABELS
    )

    work_lines = tuple(
        WorkLine.objects.filter(business=business)
        .annotate(
            future_confirmed_count=Count(
                "appointments",
                filter=Q(
                    appointments__status=Appointment.Status.CONFIRMED,
                    appointments__starts_at__gte=now,
                ),
                distinct=True,
            ),
            appointments_total=Count("appointments", distinct=True),
        )
        .order_by("display_order", "line_number", "pk")
    )
    used_line_numbers = {line.line_number for line in work_lines}
    next_available_line_number = next(
        (number for number in (1, 2, 3) if number not in used_line_numbers),
        None,
    )

    closures = tuple(
        BusinessClosure.objects.filter(business=business, date_to__gte=today)
        .select_related("work_line")
        .order_by("-is_active", "date_from", "start_time", "pk")[:10]
    )
    for closure in closures:
        closure.type_label = closure_type_label(closure.closure_type)

    calendar_settings = getattr(business, "calendar_settings", None)
    upcoming_national_holidays = tuple(
        OfficialHoliday.objects.filter(
            date__gte=today,
            scope=OfficialHoliday.Scope.NATIONAL,
        ).order_by("date", "name")[:5]
    )
    latest_holiday_sync = HolidaySyncRun.objects.filter(
        status=HolidaySyncRun.Status.SUCCESS
    ).first()

    return {
        "business": business,
        "availability_form": availability_form,
        "closure_form": closure_form,
        "work_line_form": work_line_form,
        "editing_availability": editing_availability,
        "editing_availability_label": (
            weekday_label(editing_availability.weekday)
            if editing_availability is not None
            else ""
        ),
        "editing_closure": editing_closure,
        "editing_closure_label": (
            closure_type_label(editing_closure.closure_type)
            if editing_closure is not None
            else ""
        ),
        "editing_work_line": editing_work_line,
        "weekday_sections": weekday_sections,
        "work_lines": work_lines,
        "closures": closures,
        "next_available_line_number": next_available_line_number,
        "active_availability_rules_count": sum(1 for rule in availability_rules if rule.is_active),
        "active_work_lines_count": sum(1 for line in work_lines if line.is_active),
        "active_closures_count": sum(1 for closure in closures if closure.is_active),
        "slot_interval_minutes": (
            calendar_settings.slot_interval_minutes if calendar_settings is not None else 15
        ),
        "apply_national_holidays": (
            calendar_settings.apply_national_holidays
            if calendar_settings is not None
            else True
        ),
        "upcoming_national_holidays": upcoming_national_holidays,
        "latest_holiday_sync": latest_holiday_sync,
    }

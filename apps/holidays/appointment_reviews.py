from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.booking.calendar_locking import lock_business_calendar
from apps.booking.models import Appointment
from apps.businesses.models import BusinessMembership
from apps.holidays.models import HolidayAppointmentReview, OfficialHoliday


BUSINESS_TIME_ZONE = ZoneInfo(settings.TIME_ZONE)


@dataclass(frozen=True, slots=True)
class PendingHolidayAppointment:
    appointment: Appointment
    holiday: OfficialHoliday
    local_starts_at: datetime
    local_ends_at: datetime
    review: HolidayAppointmentReview | None = None


@dataclass(frozen=True, slots=True)
class PendingHolidayBusinessSummary:
    business_id: int
    business_name: str
    business_is_active: bool
    has_active_professional: bool
    appointment_count: int


@dataclass(frozen=True, slots=True)
class HolidayReviewResult:
    review: HolidayAppointmentReview
    created: bool


def pending_holiday_appointments(*, business=None, year=None, at=None):
    effective_at = _effective_at(at)
    holidays = _future_national_holidays(effective_at, year=year)
    if not holidays:
        return tuple()

    holiday_by_date = {holiday.date: holiday for holiday in holidays}
    queryset = _pending_appointment_queryset(
        holiday_dates=tuple(holiday_by_date),
        at=effective_at,
        business=business,
    ).select_related("business", "business_client", "work_line")
    return tuple(
        PendingHolidayAppointment(
            appointment=appointment,
            holiday=holiday_by_date[appointment.holiday_local_date],
            local_starts_at=timezone.localtime(appointment.starts_at, BUSINESS_TIME_ZONE),
            local_ends_at=timezone.localtime(appointment.ends_at, BUSINESS_TIME_ZONE),
        )
        for appointment in queryset
    )


def pending_holiday_appointment_count(*, business, at=None):
    effective_at = _effective_at(at)
    holidays = _future_national_holidays(effective_at)
    if not holidays:
        return 0
    return _pending_appointment_queryset(
        holiday_dates=tuple(holiday.date for holiday in holidays),
        at=effective_at,
        business=business,
    ).count()


def pending_holiday_business_summaries(*, year=None, at=None):
    """Return only business-level aggregates; no customer data crosses this boundary."""

    effective_at = _effective_at(at)
    holidays = _future_national_holidays(effective_at, year=year)
    if not holidays:
        return tuple()

    active_professional = BusinessMembership.objects.filter(
        business_id=OuterRef("business_id"),
        is_active=True,
        user__is_active=True,
    )
    rows = (
        _pending_appointment_queryset(
            holiday_dates=tuple(holiday.date for holiday in holidays),
            at=effective_at,
        )
        .annotate(has_active_professional=Exists(active_professional))
        .values(
            "business_id",
            "business__commercial_name",
            "business__is_active",
            "has_active_professional",
        )
        .annotate(appointment_count=Count("pk"))
        .order_by("business__commercial_name", "business_id")
    )
    return tuple(
        PendingHolidayBusinessSummary(
            business_id=row["business_id"],
            business_name=row["business__commercial_name"],
            business_is_active=row["business__is_active"],
            has_active_professional=row["has_active_professional"],
            appointment_count=row["appointment_count"],
        )
        for row in rows
    )


def current_holiday_impact_for_appointment(appointment, *, at=None):
    effective_at = _effective_at(at)
    if (
        appointment.status != Appointment.Status.CONFIRMED
        or appointment.starts_at <= effective_at
        or not _business_applies_national_holidays(appointment.business)
    ):
        return None

    local_start = timezone.localtime(appointment.starts_at, BUSINESS_TIME_ZONE)
    holiday = (
        OfficialHoliday.objects.filter(
            date=local_start.date(),
            scope=OfficialHoliday.Scope.NATIONAL,
        )
        .order_by("pk")
        .first()
    )
    if holiday is None:
        return None

    review = (
        HolidayAppointmentReview.objects.filter(
            appointment=appointment,
            holiday_date=holiday.date,
        )
        .select_related("reviewed_by")
        .first()
    )
    return PendingHolidayAppointment(
        appointment=appointment,
        holiday=holiday,
        local_starts_at=local_start,
        local_ends_at=timezone.localtime(appointment.ends_at, BUSINESS_TIME_ZONE),
        review=review,
    )


def acknowledge_holiday_appointment(*, business, appointment_id, reviewed_by, at=None):
    """Record that the professional deliberately keeps an existing holiday appointment."""

    if reviewed_by is None or not reviewed_by.is_authenticated:
        raise ValidationError("Necesitas identificarte para revisar esta cita.")
    requested_at = _effective_at(at) if at is not None else None

    with transaction.atomic():
        locked_calendar = lock_business_calendar(business)
        # A mutex wait may cross the appointment start. Re-read real time only
        # after acquiring it; explicit ``at`` remains available for deterministic tests.
        effective_at = requested_at or timezone.now()
        if not locked_calendar.settings.apply_national_holidays:
            raise ValidationError(
                "Este negocio no aplica ahora mismo los festivos nacionales."
            )

        appointment = (
            Appointment.objects.select_for_update()
            .filter(pk=appointment_id, business=locked_calendar.business)
            .first()
        )
        if appointment is None:
            raise ValidationError("La cita ya no está disponible para esta revisión.")
        if appointment.status != Appointment.Status.CONFIRMED:
            raise ValidationError(
                "La cita ya no está confirmada y ha dejado de estar pendiente de revisión."
            )
        if appointment.starts_at <= effective_at:
            raise ValidationError(
                "La cita ya ha comenzado y ya no puede marcarse como revisada desde aquí."
            )

        local_start = timezone.localtime(appointment.starts_at, BUSINESS_TIME_ZONE)
        holiday = (
            OfficialHoliday.objects.select_for_update()
            .filter(
                date=local_start.date(),
                scope=OfficialHoliday.Scope.NATIONAL,
            )
            .order_by("pk")
            .first()
        )
        if holiday is None:
            raise ValidationError("La cita ya no coincide con un festivo nacional vigente.")

        existing = (
            HolidayAppointmentReview.objects.select_for_update()
            .filter(appointment=appointment, holiday_date=holiday.date)
            .first()
        )
        if existing is not None:
            return HolidayReviewResult(review=existing, created=False)

        review = HolidayAppointmentReview(
            appointment=appointment,
            holiday=holiday,
            holiday_date=holiday.date,
            holiday_name=holiday.name,
            reviewed_by=reviewed_by,
            reviewed_at=effective_at,
        )
        review.full_clean()
        review.save()
        return HolidayReviewResult(review=review, created=True)


def _pending_appointment_queryset(*, holiday_dates, at, business=None):
    matching_review = HolidayAppointmentReview.objects.filter(
        appointment_id=OuterRef("pk"),
        holiday_date=OuterRef("holiday_local_date"),
    )
    queryset = (
        Appointment.objects.annotate(
            holiday_local_date=TruncDate("starts_at", tzinfo=BUSINESS_TIME_ZONE),
        )
        .annotate(
            has_matching_holiday_review=Exists(matching_review),
        )
        .filter(
            status=Appointment.Status.CONFIRMED,
            starts_at__gt=at,
            holiday_local_date__in=holiday_dates,
            has_matching_holiday_review=False,
        )
        .filter(
            Q(business__calendar_settings__apply_national_holidays=True)
            | Q(business__calendar_settings__isnull=True)
        )
    )
    if business is not None:
        queryset = queryset.filter(business=business)
    return queryset.order_by("holiday_local_date", "starts_at", "pk")


def _future_national_holidays(at, *, year=None):
    queryset = OfficialHoliday.objects.filter(
        date__gte=timezone.localtime(at, BUSINESS_TIME_ZONE).date(),
        scope=OfficialHoliday.Scope.NATIONAL,
    )
    if year is not None:
        queryset = queryset.filter(year=year)
    return tuple(queryset.order_by("date", "pk"))


def _business_applies_national_holidays(business):
    try:
        return business.calendar_settings.apply_national_holidays
    except ObjectDoesNotExist:
        return True


def _effective_at(at):
    value = at or timezone.now()
    if timezone.is_naive(value):
        value = timezone.make_aware(value, BUSINESS_TIME_ZONE)
    return value

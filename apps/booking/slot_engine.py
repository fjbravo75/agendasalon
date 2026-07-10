from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from apps.booking.models import Appointment, AvailabilityRule, BusinessClosure, WorkLine
from apps.holidays.models import OfficialHoliday


STATUS_AVAILABLE = "available"
STATUS_PAST = "past"
STATUS_CLOSED = "closed"
STATUS_UNAVAILABLE = "unavailable"

CHANNEL_PROFESSIONAL = "professional"
CHANNEL_PUBLIC = "public"


@dataclass(frozen=True)
class Slot:
    work_line_id: int
    work_line_name: str
    work_line_number: int
    starts_at: datetime
    ends_at: datetime
    duration_minutes: int
    score: int = 0
    reason: str = "valido"


@dataclass(frozen=True)
class DayAvailability:
    date: date
    duration_minutes: int
    calculated_from: datetime | None
    slots_by_line: dict[int, tuple[Slot, ...]]
    status: str
    reason: str = ""

    @property
    def slots(self) -> tuple[Slot, ...]:
        return _rank_slots(slot for line_slots in self.slots_by_line.values() for slot in line_slots)

    @property
    def has_slots(self) -> bool:
        return bool(self.slots)


@dataclass(frozen=True)
class MonthDayAvailability:
    date: date
    status: str
    first_slot: Slot | None = None
    reason: str = ""

    @property
    def is_available(self) -> bool:
        return self.status == STATUS_AVAILABLE


@dataclass(frozen=True)
class MonthAvailability:
    year: int
    month: int
    duration_minutes: int
    days: tuple[MonthDayAvailability, ...]

    @property
    def available_dates(self) -> tuple[date, ...]:
        return tuple(day.date for day in self.days if day.is_available)


def get_day_availability(
    *,
    business,
    target_date: date,
    duration_minutes: int,
    now: datetime | None = None,
) -> DayAvailability:
    tz = _project_timezone()
    now_local = _coerce_now(now, tz)
    slot_interval = _slot_interval_minutes(business)
    _validate_duration(duration_minutes, slot_interval)

    active_lines = _active_work_lines(business)
    empty_slots_by_line = {line.id: tuple() for line in active_lines}
    calculated_from = _calculated_from(target_date, now_local, slot_interval)

    if target_date < now_local.date():
        return DayAvailability(
            date=target_date,
            duration_minutes=duration_minutes,
            calculated_from=calculated_from,
            slots_by_line=empty_slots_by_line,
            status=STATUS_PAST,
            reason="dia_pasado",
        )

    if not business.is_active:
        return _closed_day(target_date, duration_minutes, calculated_from, empty_slots_by_line, "negocio_inactivo")

    if not active_lines:
        return _closed_day(target_date, duration_minutes, calculated_from, empty_slots_by_line, "sin_lineas_activas")

    if _is_national_holiday(business, target_date):
        return _closed_day(target_date, duration_minutes, calculated_from, empty_slots_by_line, "festivo_nacional")

    availability_intervals = _availability_intervals(business, target_date, tz)
    if not availability_intervals:
        return _closed_day(target_date, duration_minutes, calculated_from, empty_slots_by_line, "sin_horario")

    if calculated_from and calculated_from.date() == target_date:
        availability_intervals = _trim_intervals_before(availability_intervals, calculated_from)
        if not availability_intervals:
            return DayAvailability(
                date=target_date,
                duration_minutes=duration_minutes,
                calculated_from=calculated_from,
                slots_by_line=empty_slots_by_line,
                status=STATUS_UNAVAILABLE,
                reason="fuera_de_horario",
            )

    closures = _closures_for_date(business, target_date)
    if _has_full_business_closure(closures):
        return _closed_day(target_date, duration_minutes, calculated_from, empty_slots_by_line, "cierre_negocio")

    day_start, day_end = _day_bounds(target_date, tz)
    appointments_by_line = _appointments_by_line(business, active_lines, day_start, day_end, tz)
    slots_by_line = {}

    for line in active_lines:
        busy_intervals = _busy_intervals_for_line(
            line=line,
            closures=closures,
            appointments=appointments_by_line.get(line.id, ()),
            target_date=target_date,
            tz=tz,
            day_start=day_start,
            day_end=day_end,
        )
        free_intervals = _subtract_busy_intervals(availability_intervals, busy_intervals)
        slots_by_line[line.id] = _slots_from_free_intervals(
            line=line,
            free_intervals=free_intervals,
            duration_minutes=duration_minutes,
            slot_interval=slot_interval,
        )

    status = STATUS_AVAILABLE if any(slots_by_line.values()) else STATUS_UNAVAILABLE
    reason = "" if status == STATUS_AVAILABLE else "sin_hueco"
    return DayAvailability(
        date=target_date,
        duration_minutes=duration_minutes,
        calculated_from=calculated_from,
        slots_by_line=slots_by_line,
        status=status,
        reason=reason,
    )


def get_month_availability(
    *,
    business,
    year: int,
    month: int,
    duration_minutes: int,
    now: datetime | None = None,
) -> MonthAvailability:
    _, last_day = monthrange(year, month)
    days = []

    for day_number in range(1, last_day + 1):
        current_date = date(year, month, day_number)
        day_availability = get_day_availability(
            business=business,
            target_date=current_date,
            duration_minutes=duration_minutes,
            now=now,
        )
        first_slot = day_availability.slots[0] if day_availability.has_slots else None
        status = STATUS_AVAILABLE if first_slot else day_availability.status
        days.append(
            MonthDayAvailability(
                date=current_date,
                status=status,
                first_slot=first_slot,
                reason=day_availability.reason,
            )
        )

    return MonthAvailability(
        year=year,
        month=month,
        duration_minutes=duration_minutes,
        days=tuple(days),
    )


def suggest_next_slots(
    *,
    business,
    start_date: date,
    duration_minutes: int,
    now: datetime | None = None,
    days_ahead: int = 14,
    limit: int = 3,
) -> tuple[Slot, ...]:
    tz = _project_timezone()
    now_local = _coerce_now(now, tz)
    first_search_date = max(start_date, now_local.date())
    suggestions = []

    for offset in range(days_ahead + 1):
        target_date = first_search_date + timedelta(days=offset)
        day_availability = get_day_availability(
            business=business,
            target_date=target_date,
            duration_minutes=duration_minutes,
            now=now_local,
        )
        suggestions.extend(day_availability.slots)
        if len(suggestions) >= limit:
            break

    return tuple(sorted(suggestions, key=_suggestion_sort_key)[:limit])


def get_booking_options(
    *,
    business,
    start_date: date,
    duration_minutes: int,
    now: datetime | None = None,
    channel: str = CHANNEL_PROFESSIONAL,
    days_ahead: int = 14,
    limit: int | None = None,
) -> tuple[Slot, ...]:
    if limit is None:
        limit = 6 if channel == CHANNEL_PROFESSIONAL else 4
    slots = suggest_next_slots(
        business=business,
        start_date=start_date,
        duration_minutes=duration_minutes,
        now=now,
        days_ahead=days_ahead,
        limit=limit * 3 if channel == CHANNEL_PUBLIC else limit,
    )
    if channel == CHANNEL_PUBLIC:
        slots = _collapse_public_slots(slots)
    return slots[:limit]


def _project_timezone() -> ZoneInfo:
    return ZoneInfo(settings.TIME_ZONE)


def _coerce_now(now: datetime | None, tz: ZoneInfo) -> datetime:
    if now is None:
        now = timezone.now()
    if timezone.is_naive(now):
        raise ValueError("now debe ser timezone-aware.")
    return timezone.localtime(now, tz)


def _slot_interval_minutes(business) -> int:
    try:
        return business.calendar_settings.slot_interval_minutes
    except ObjectDoesNotExist:
        return 15


def _applies_national_holidays(business) -> bool:
    try:
        return business.calendar_settings.apply_national_holidays
    except ObjectDoesNotExist:
        return True


def _validate_duration(duration_minutes: int, slot_interval: int) -> None:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes debe ser positivo.")
    if duration_minutes % slot_interval != 0:
        raise ValueError("duration_minutes debe ser compatible con el intervalo de agenda.")


def _calculated_from(target_date: date, now_local: datetime, slot_interval: int) -> datetime | None:
    if target_date == now_local.date():
        return _ceil_to_slot(now_local, slot_interval)
    return None


def _closed_day(
    target_date: date,
    duration_minutes: int,
    calculated_from: datetime | None,
    slots_by_line: dict[int, tuple[Slot, ...]],
    reason: str,
) -> DayAvailability:
    return DayAvailability(
        date=target_date,
        duration_minutes=duration_minutes,
        calculated_from=calculated_from,
        slots_by_line=slots_by_line,
        status=STATUS_CLOSED,
        reason=reason,
    )


def _active_work_lines(business) -> tuple[WorkLine, ...]:
    return tuple(
        WorkLine.objects.filter(
            business=business,
            is_active=True,
        ).order_by("display_order", "line_number", "pk")
    )


def _is_national_holiday(business, target_date: date) -> bool:
    if not _applies_national_holidays(business):
        return False
    return OfficialHoliday.objects.filter(
        date=target_date,
        scope=OfficialHoliday.Scope.NATIONAL,
    ).exists()


def _availability_intervals(business, target_date: date, tz: ZoneInfo) -> tuple[tuple[datetime, datetime], ...]:
    intervals = []
    rules = AvailabilityRule.objects.filter(
        business=business,
        weekday=target_date.weekday(),
        is_active=True,
    ).order_by("start_time", "pk")

    for rule in rules:
        intervals.append(
            (
                _combine(target_date, rule.start_time, tz),
                _combine(target_date, rule.end_time, tz),
            )
        )

    return tuple(intervals)


def _closures_for_date(business, target_date: date) -> tuple[BusinessClosure, ...]:
    return tuple(
        BusinessClosure.objects.select_related("work_line").filter(
            business=business,
            is_active=True,
            date_from__lte=target_date,
            date_to__gte=target_date,
        )
    )


def _has_full_business_closure(closures: tuple[BusinessClosure, ...]) -> bool:
    return any(closure.work_line_id is None and not closure.start_time and not closure.end_time for closure in closures)


def _day_bounds(target_date: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
    day_start = _combine(target_date, time.min, tz)
    return day_start, day_start + timedelta(days=1)


def _appointments_by_line(
    business,
    active_lines: tuple[WorkLine, ...],
    day_start: datetime,
    day_end: datetime,
    tz: ZoneInfo,
) -> dict[int, tuple[Appointment, ...]]:
    line_ids = [line.id for line in active_lines]
    appointments = (
        Appointment.objects.filter(
            business=business,
            work_line_id__in=line_ids,
            status=Appointment.Status.CONFIRMED,
            starts_at__lt=day_end,
            ends_at__gt=day_start,
        )
        .select_related("work_line")
        .order_by("starts_at", "pk")
    )

    grouped = {line_id: [] for line_id in line_ids}
    for appointment in appointments:
        appointment.starts_at = timezone.localtime(appointment.starts_at, tz)
        appointment.ends_at = timezone.localtime(appointment.ends_at, tz)
        grouped.setdefault(appointment.work_line_id, []).append(appointment)
    return {line_id: tuple(items) for line_id, items in grouped.items()}


def _busy_intervals_for_line(
    *,
    line: WorkLine,
    closures: tuple[BusinessClosure, ...],
    appointments: tuple[Appointment, ...],
    target_date: date,
    tz: ZoneInfo,
    day_start: datetime,
    day_end: datetime,
) -> tuple[tuple[datetime, datetime], ...]:
    intervals = []

    for closure in closures:
        if closure.work_line_id not in (None, line.id):
            continue
        intervals.append(_closure_interval(closure, target_date, tz, day_start, day_end))

    for appointment in appointments:
        intervals.append(
            (
                max(appointment.starts_at, day_start),
                min(appointment.ends_at, day_end),
            )
        )

    return _merge_intervals(intervals)


def _closure_interval(
    closure: BusinessClosure,
    target_date: date,
    tz: ZoneInfo,
    day_start: datetime,
    day_end: datetime,
) -> tuple[datetime, datetime]:
    if closure.start_time and closure.end_time:
        return (
            max(_combine(target_date, closure.start_time, tz), day_start),
            min(_combine(target_date, closure.end_time, tz), day_end),
        )
    return day_start, day_end


def _subtract_busy_intervals(
    availability_intervals: tuple[tuple[datetime, datetime], ...],
    busy_intervals: tuple[tuple[datetime, datetime], ...],
) -> tuple[tuple[datetime, datetime], ...]:
    free_intervals = []

    for availability_start, availability_end in availability_intervals:
        cursor = availability_start
        for busy_start, busy_end in busy_intervals:
            if busy_end <= cursor or busy_start >= availability_end:
                continue
            if busy_start > cursor:
                free_intervals.append((cursor, min(busy_start, availability_end)))
            cursor = max(cursor, busy_end)
            if cursor >= availability_end:
                break
        if cursor < availability_end:
            free_intervals.append((cursor, availability_end))

    return tuple(free_intervals)


def _slots_from_free_intervals(
    *,
    line: WorkLine,
    free_intervals: tuple[tuple[datetime, datetime], ...],
    duration_minutes: int,
    slot_interval: int,
) -> tuple[Slot, ...]:
    slots = []
    duration = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=slot_interval)
    line_name = line.name or f"Línea {line.line_number}"

    for free_start, free_end in free_intervals:
        starts_at = _ceil_to_slot(free_start, slot_interval)
        while starts_at + duration <= free_end:
            ends_at = starts_at + duration
            score, reason = _score_slot(
                starts_at=starts_at,
                ends_at=ends_at,
                free_start=free_start,
                free_end=free_end,
                duration_minutes=duration_minutes,
            )
            slots.append(
                Slot(
                    work_line_id=line.id,
                    work_line_name=line_name,
                    work_line_number=line.line_number,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    duration_minutes=duration_minutes,
                    score=score,
                    reason=reason,
                )
            )
            starts_at += step

    return tuple(slots)


def _score_slot(
    *,
    starts_at: datetime,
    ends_at: datetime,
    free_start: datetime,
    free_end: datetime,
    duration_minutes: int,
) -> tuple[int, str]:
    before_gap = _minutes_between(free_start, starts_at)
    after_gap = _minutes_between(ends_at, free_end)
    smallest_useful_gap = min(duration_minutes, 60)
    score = 1000
    reason = "hueco_valido"

    if before_gap == 0 and after_gap == 0:
        return 1240, "rellena_hueco_exacto"

    if before_gap == 0 or after_gap == 0:
        score += 120
        reason = "compacta_agenda"

    if 0 < before_gap < smallest_useful_gap:
        score -= 140
        reason = "evita_restos_pequenos"
    if 0 < after_gap < smallest_useful_gap:
        score -= 140
        reason = "evita_restos_pequenos"

    if before_gap >= smallest_useful_gap:
        score += 15
    if after_gap >= smallest_useful_gap:
        score += 15

    return score, reason


def _rank_slots(slots) -> tuple[Slot, ...]:
    return tuple(sorted(slots, key=lambda slot: (-slot.score, slot.starts_at, slot.work_line_number, slot.work_line_id)))


def _suggestion_sort_key(slot: Slot):
    return (slot.starts_at.date(), -slot.score, slot.starts_at, slot.work_line_number, slot.work_line_id)


def _collapse_public_slots(slots: tuple[Slot, ...]) -> tuple[Slot, ...]:
    unique = {}
    for slot in slots:
        current = unique.get(slot.starts_at)
        if current is None or _suggestion_sort_key(slot) < _suggestion_sort_key(current):
            unique[slot.starts_at] = slot
    return tuple(sorted(unique.values(), key=_suggestion_sort_key))


def _minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> tuple[tuple[datetime, datetime], ...]:
    clean_intervals = sorted(
        ((start, end) for start, end in intervals if start < end),
        key=lambda interval: interval[0],
    )
    if not clean_intervals:
        return tuple()

    merged = [clean_intervals[0]]
    for start, end in clean_intervals[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def _trim_intervals_before(
    intervals: tuple[tuple[datetime, datetime], ...],
    threshold: datetime,
) -> tuple[tuple[datetime, datetime], ...]:
    trimmed = []
    for starts_at, ends_at in intervals:
        if ends_at <= threshold:
            continue
        trimmed.append((max(starts_at, threshold), ends_at))
    return tuple(trimmed)


def _ceil_to_slot(value: datetime, slot_interval: int) -> datetime:
    had_seconds = bool(value.second or value.microsecond)
    value = value.replace(second=0, microsecond=0)
    minutes = value.hour * 60 + value.minute
    remainder = minutes % slot_interval
    if remainder:
        value += timedelta(minutes=slot_interval - remainder)
    elif had_seconds:
        value += timedelta(minutes=slot_interval)
    return value


def _combine(target_date: date, value: time, tz: ZoneInfo) -> datetime:
    return timezone.make_aware(datetime.combine(target_date, value), tz)

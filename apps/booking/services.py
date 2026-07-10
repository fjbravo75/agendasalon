from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.booking.models import Appointment, AppointmentService, Service, WorkLine
from apps.booking.slot_engine import get_day_availability
from apps.customers.models import BusinessClient


@dataclass(frozen=True)
class AppointmentDraft:
    business: object
    business_client: BusinessClient
    services: tuple[Service, ...]
    work_line_id: int
    starts_at: datetime
    duration_minutes: int
    channel: str
    created_by: object | None = None
    duration_adjustment_reason: str = ""


@transaction.atomic
def confirm_appointment(draft: AppointmentDraft) -> Appointment:
    services = tuple(draft.services)
    if not services:
        raise ValidationError("Selecciona al menos un servicio.")

    service_ids = [service.id for service in services]
    active_services = tuple(
        Service.objects.filter(
            business=draft.business,
            is_active=True,
            id__in=service_ids,
        ).order_by("display_order", "name", "pk")
    )
    if {service.id for service in active_services} != set(service_ids):
        raise ValidationError("Alguno de los servicios ya no esta disponible.")

    work_line = WorkLine.objects.select_for_update().get(
        business=draft.business,
        is_active=True,
        id=draft.work_line_id,
    )
    starts_at = timezone.localtime(draft.starts_at)
    ends_at = starts_at + timedelta(minutes=draft.duration_minutes)

    availability = get_day_availability(
        business=draft.business,
        target_date=starts_at.date(),
        duration_minutes=draft.duration_minutes,
    )
    matching_slot = next(
        (
            slot
            for slot in availability.slots
            if slot.work_line_id == work_line.id and slot.starts_at == starts_at
        ),
        None,
    )
    if matching_slot is None:
        raise ValidationError("Ese hueco ya no esta disponible. Elige otro horario.")

    appointment = Appointment(
        business=draft.business,
        business_client=draft.business_client,
        work_line=work_line,
        starts_at=starts_at,
        ends_at=ends_at,
        total_duration_minutes=draft.duration_minutes,
        duration_adjustment_reason=draft.duration_adjustment_reason,
        status=Appointment.Status.CONFIRMED,
        manual_channel=draft.channel,
        created_by=draft.created_by,
        service_summary_snapshot=" + ".join(service.name for service in active_services),
    )
    appointment.full_clean()
    appointment.save()

    for order, service in enumerate(active_services, start=1):
        item = AppointmentService(
            appointment=appointment,
            service=service,
            display_order=order,
            service_name_snapshot=service.name,
            duration_minutes_snapshot=service.duration_minutes,
            price_amount_snapshot=service.price_amount,
            color_hex_snapshot=service.color_hex,
        )
        item.full_clean()
        item.save()

    appointment.full_clean()
    return appointment


@transaction.atomic
def cancel_appointment(appointment: Appointment, *, cancelled_by, reason: str) -> Appointment:
    if appointment.status != Appointment.Status.CONFIRMED:
        raise ValidationError("Solo se puede cancelar una cita confirmada.")

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Indica el motivo de cancelación.")

    appointment.status = Appointment.Status.CANCELLED
    appointment.cancelled_by = cancelled_by
    appointment.cancelled_at = timezone.now()
    appointment.cancellation_reason = reason
    appointment.full_clean()
    appointment.save(
        update_fields=[
            "status",
            "cancelled_by",
            "cancelled_at",
            "cancellation_reason",
            "updated_at",
        ]
    )
    return appointment


@transaction.atomic
def complete_appointment(appointment: Appointment, *, completed_by) -> Appointment:
    if appointment.status != Appointment.Status.CONFIRMED:
        raise ValidationError("Solo se puede completar una cita confirmada.")
    if appointment.starts_at > timezone.now():
        raise ValidationError("No se puede completar una cita que todavía no ha empezado.")

    appointment.status = Appointment.Status.COMPLETED
    appointment.completed_by = completed_by
    appointment.completed_at = timezone.now()
    appointment.full_clean()
    appointment.save(
        update_fields=[
            "status",
            "completed_by",
            "completed_at",
            "updated_at",
        ]
    )
    return appointment

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.booking.models import Appointment, AppointmentService, Service, WorkLine
from apps.booking.slot_engine import get_day_availability
from apps.businesses.activity import record_business_activity
from apps.businesses.models import BusinessActivityEvent
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
        raise ValidationError("Alguno de los servicios ya no está disponible.")

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
        raise ValidationError("Ese hueco ya no está disponible. Elige otro horario.")

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
    is_public_booking = draft.channel == Appointment.ManualChannel.PUBLIC_WEB
    record_business_activity(
        business=draft.business,
        category=BusinessActivityEvent.Category.APPOINTMENTS,
        event_type=BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
        origin=draft.channel,
        summary=(
            f"Reserva online creada para {_appointment_moment(appointment)}."
            if is_public_booking
            else f"Cita creada por el equipo para {_appointment_moment(appointment)}."
        ),
        actor=draft.created_by,
        actor_type=(BusinessActivityEvent.ActorType.CUSTOMER if is_public_booking else None),
        actor_label=("Cliente online" if is_public_booking else None),
        entity=appointment,
        entity_type="appointment",
        changes={
            "status": appointment.status,
            "origin": appointment.manual_channel,
            "starts_at": appointment.starts_at.isoformat(),
        },
    )
    return appointment


@transaction.atomic
def cancel_appointment(appointment: Appointment, *, cancelled_by, reason: str) -> Appointment:
    appointment = _locked_appointment(appointment)
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
    record_business_activity(
        business=appointment.business,
        category=BusinessActivityEvent.Category.APPOINTMENTS,
        event_type=BusinessActivityEvent.EventType.APPOINTMENT_CANCELLED,
        origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
        summary=f"Cita cancelada para {_appointment_moment(appointment)}.",
        actor=cancelled_by,
        entity=appointment,
        entity_type="appointment",
        changes={"status": appointment.status},
    )
    return appointment


@transaction.atomic
def complete_appointment(appointment: Appointment, *, completed_by, at=None) -> Appointment:
    appointment = _locked_appointment(appointment)
    at = at or timezone.now()
    if appointment.status != Appointment.Status.CONFIRMED:
        raise ValidationError("Solo se puede completar una cita confirmada.")
    if appointment.starts_at > at:
        raise ValidationError("No se puede completar una cita que todavía no ha empezado.")

    appointment.status = Appointment.Status.COMPLETED
    appointment.completed_by = completed_by
    appointment.completed_at = at
    appointment.full_clean()
    appointment.save(
        update_fields=[
            "status",
            "completed_by",
            "completed_at",
            "updated_at",
        ]
    )
    record_business_activity(
        business=appointment.business,
        category=BusinessActivityEvent.Category.APPOINTMENTS,
        event_type=BusinessActivityEvent.EventType.APPOINTMENT_COMPLETED,
        origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
        summary=f"Cita marcada como atendida para {_appointment_moment(appointment)}.",
        actor=completed_by,
        entity=appointment,
        entity_type="appointment",
        changes={"status": appointment.status},
    )
    return appointment


@transaction.atomic
def mark_appointment_no_show(appointment: Appointment, *, marked_by, at=None) -> Appointment:
    appointment = _locked_appointment(appointment)
    at = at or timezone.now()
    if appointment.status != Appointment.Status.CONFIRMED:
        raise ValidationError("Solo se puede registrar la ausencia de una cita confirmada.")
    if appointment.starts_at > at:
        raise ValidationError("No se puede registrar la ausencia antes de que empiece la cita.")

    appointment.status = Appointment.Status.NO_SHOW
    appointment.no_show_marked_by = marked_by
    appointment.no_show_marked_at = at
    appointment.full_clean()
    appointment.save(
        update_fields=[
            "status",
            "no_show_marked_by",
            "no_show_marked_at",
            "updated_at",
        ]
    )
    record_business_activity(
        business=appointment.business,
        category=BusinessActivityEvent.Category.APPOINTMENTS,
        event_type=BusinessActivityEvent.EventType.APPOINTMENT_NO_SHOW,
        origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
        summary=f"Ausencia registrada para la cita de {_appointment_moment(appointment)}.",
        actor=marked_by,
        entity=appointment,
        entity_type="appointment",
        changes={"status": appointment.status},
    )
    return appointment


@transaction.atomic
def close_appointments(appointments, *, outcome, closed_by, at=None) -> int:
    at = at or timezone.now()
    appointments = tuple(sorted(appointments, key=lambda appointment: appointment.pk))
    if outcome not in {Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW}:
        raise ValidationError("El resultado elegido no es válido.")

    for appointment in appointments:
        if outcome == Appointment.Status.COMPLETED:
            complete_appointment(appointment, completed_by=closed_by, at=at)
        else:
            mark_appointment_no_show(appointment, marked_by=closed_by, at=at)
    return len(appointments)


def _locked_appointment(appointment: Appointment) -> Appointment:
    """Reload and lock the current row before applying an outcome transition."""
    if appointment.pk is None:
        raise ValidationError("La cita debe estar guardada antes de actualizar su estado.")
    return Appointment.objects.select_for_update().get(pk=appointment.pk)


def _appointment_moment(appointment):
    starts_at = timezone.localtime(appointment.starts_at)
    return starts_at.strftime("%d/%m/%Y a las %H:%M")

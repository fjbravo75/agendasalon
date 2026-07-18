from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.booking.calendar_locking import lock_business_calendar
from apps.booking.models import Appointment, AppointmentService, Service
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
    requested_by_client_access: object | None = None
    requested_by_name: str = ""
    requested_by_relationship: str = ""
    public_confirmation_reference: UUID | str | None = None


@transaction.atomic
def confirm_appointment(
    draft: AppointmentDraft,
    *,
    locked_calendar=None,
    allow_line_reassignment=False,
) -> Appointment:
    services = tuple(draft.services)
    if not services:
        raise ValidationError("Selecciona al menos un servicio.")

    if locked_calendar is None:
        locked_calendar = lock_business_calendar(draft.business)
    elif locked_calendar.business.pk != draft.business.pk:
        raise ValidationError("No se ha podido validar la agenda de este negocio.")
    business = locked_calendar.business
    slot_interval = locked_calendar.settings.slot_interval_minutes
    duration_minutes = draft.duration_minutes
    if (
        isinstance(duration_minutes, bool)
        or not isinstance(duration_minutes, int)
        or duration_minutes <= 0
    ):
        raise ValidationError("La duración de la cita debe ser un número positivo de minutos.")
    if duration_minutes % slot_interval != 0:
        raise ValidationError(
            "La duración de la cita debe ser compatible con el intervalo de agenda "
            f"de {slot_interval} minutos."
        )
    if not isinstance(draft.starts_at, datetime) or timezone.is_naive(draft.starts_at):
        raise ValidationError("La hora seleccionada no es válida.")

    confirmation_reference = _public_confirmation_reference(draft)

    requested_work_line = locked_calendar.work_lines_by_id.get(draft.work_line_id)
    if (
        not allow_line_reassignment
        and (requested_work_line is None or not requested_work_line.is_active)
    ):
        raise ValidationError(
            "Ese hueco ya no está disponible. Elige otra línea u otro horario."
        )

    service_ids = [service.id for service in services]
    if None in service_ids or len(service_ids) != len(set(service_ids)):
        raise ValidationError("La selección de servicios no es válida.")

    starts_at = timezone.localtime(draft.starts_at)
    if confirmation_reference is not None:
        existing_appointment = (
            Appointment.objects.filter(
                public_confirmation_reference=confirmation_reference,
            )
            .prefetch_related("appointment_services")
            .first()
        )
        if existing_appointment is not None:
            _validate_public_confirmation_replay(
                existing_appointment,
                draft=draft,
                business=business,
                service_ids=service_ids,
                starts_at=starts_at,
                duration_minutes=duration_minutes,
            )
            return existing_appointment

    active_services = tuple(
        Service.objects.filter(
            business=business,
            is_active=True,
            id__in=service_ids,
        ).order_by("display_order", "name", "pk")
    )
    if {service.id for service in active_services} != set(service_ids):
        raise ValidationError("Alguno de los servicios ya no está disponible.")
    incompatible_service = next(
        (
            service
            for service in active_services
            if service.duration_minutes % slot_interval != 0
        ),
        None,
    )
    if incompatible_service is not None:
        raise ValidationError(
            f'El servicio "{incompatible_service.name}" ya no es compatible con el '
            f"intervalo de agenda de {slot_interval} minutos."
        )

    services_duration = sum(service.duration_minutes for service in active_services)
    adjustment_reason = (draft.duration_adjustment_reason or "").strip()
    if duration_minutes != services_duration and not adjustment_reason:
        raise ValidationError(
            "Indica el motivo del ajuste cuando la duración de la cita no coincide "
            "con la suma de los servicios."
        )

    ends_at = starts_at + timedelta(minutes=duration_minutes)

    try:
        availability = get_day_availability(
            business=business,
            target_date=starts_at.date(),
            duration_minutes=duration_minutes,
        )
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "No se ha podido calcular ese hueco con la duración indicada."
        ) from exc
    matching_slot = next(
        (
            slot
            for slot in availability.slots
            if slot.work_line_id == draft.work_line_id and slot.starts_at == starts_at
        ),
        None,
    )
    if matching_slot is None and allow_line_reassignment:
        matching_slot = next(
            (slot for slot in availability.slots if slot.starts_at == starts_at),
            None,
        )
    if matching_slot is None:
        if allow_line_reassignment:
            raise ValidationError(
                "Esa hora acaba de ocuparse. Te mostramos las siguientes opciones disponibles."
            )
        raise ValidationError("Ese hueco ya no está disponible. Elige otro horario.")

    work_line = locked_calendar.work_lines_by_id.get(matching_slot.work_line_id)
    if work_line is None or not work_line.is_active:
        raise ValidationError("Ese hueco ya no está disponible. Elige otro horario.")

    appointment = Appointment(
        business=business,
        business_client=draft.business_client,
        work_line=work_line,
        starts_at=starts_at,
        ends_at=ends_at,
        total_duration_minutes=duration_minutes,
        duration_adjustment_reason=adjustment_reason,
        status=Appointment.Status.CONFIRMED,
        manual_channel=draft.channel,
        created_by=draft.created_by,
        requested_by_client_access=draft.requested_by_client_access,
        requested_by_name_snapshot=draft.requested_by_name,
        requested_by_relationship_snapshot=draft.requested_by_relationship,
        public_confirmation_reference=confirmation_reference,
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
        business=business,
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
    from apps.notifications.services import (
        dispatch_outbound_email,
        queue_appointment_emails,
        queue_operational_notice_on_commit,
    )

    if is_public_booking:
        queue_operational_notice_on_commit(
            scope="business",
            code="new_appointment",
            deduplication_key=f"new-appointment:{appointment.pk}",
            business=business,
            action_path=reverse(
                "booking:professional_appointment_detail",
                args=[appointment.pk],
            ),
        )

    queued_email_ids = [email.pk for email in queue_appointment_emails(appointment)]
    transaction.on_commit(
        lambda: [dispatch_outbound_email(email_id) for email_id in queued_email_ids]
    )
    return appointment


def _public_confirmation_reference(draft: AppointmentDraft) -> UUID | None:
    value = draft.public_confirmation_reference
    if value in (None, ""):
        return None
    if draft.channel != Appointment.ManualChannel.PUBLIC_WEB:
        raise ValidationError(
            "La referencia de confirmación solo puede usarse en una reserva online."
        )
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("La referencia de confirmación no es válida.") from exc


def _validate_public_confirmation_replay(
    appointment: Appointment,
    *,
    draft: AppointmentDraft,
    business,
    service_ids,
    starts_at,
    duration_minutes,
) -> None:
    """Return an existing booking only when it belongs to the exact same draft."""

    requested_access_id = getattr(draft.requested_by_client_access, "pk", None)
    stored_service_ids = {
        item.service_id for item in appointment.appointment_services.all()
    }
    same_draft = (
        appointment.business_id == business.pk
        and appointment.business_client_id == draft.business_client.pk
        and appointment.manual_channel == Appointment.ManualChannel.PUBLIC_WEB
        and appointment.status == Appointment.Status.CONFIRMED
        and appointment.requested_by_client_access_id == requested_access_id
        and appointment.starts_at == starts_at
        and appointment.total_duration_minutes == duration_minutes
        and stored_service_ids == set(service_ids)
    )
    if not same_draft:
        raise ValidationError(
            "No se ha podido verificar esta confirmación. Vuelve a elegir la hora."
        )


@transaction.atomic
def cancel_appointment(appointment: Appointment, *, cancelled_by, reason: str) -> Appointment:
    appointment = _locked_appointment_with_calendar(appointment)
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
    from apps.notifications.services import (
        cancel_appointment_emails,
        queue_operational_notice_on_commit,
    )

    cancel_appointment_emails(appointment)
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
    queue_operational_notice_on_commit(
        scope="business",
        code="cancellation",
        deduplication_key=f"appointment-cancelled:{appointment.pk}",
        business=appointment.business,
        action_path=reverse(
            "booking:professional_appointment_detail",
            args=[appointment.pk],
        ),
    )
    return appointment


@transaction.atomic
def complete_appointment(appointment: Appointment, *, completed_by, at=None) -> Appointment:
    appointment = _locked_appointment_with_calendar(appointment)
    at = at or timezone.now()
    if appointment.status != Appointment.Status.CONFIRMED:
        raise ValidationError("Solo se puede completar una cita confirmada.")
    if appointment.starts_at > at:
        raise ValidationError("No se puede completar una cita que todavía no ha empezado.")
    if appointment.ends_at > at:
        raise ValidationError("No se puede completar una cita que todavía no ha terminado.")

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
    appointment = _locked_appointment_with_calendar(appointment)
    at = at or timezone.now()
    if appointment.status != Appointment.Status.CONFIRMED:
        raise ValidationError("Solo se puede registrar la ausencia de una cita confirmada.")
    if appointment.starts_at > at:
        raise ValidationError("No se puede registrar la ausencia antes de que empiece la cita.")
    if appointment.ends_at > at:
        raise ValidationError("No se puede registrar la ausencia porque la cita todavía no ha terminado.")

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
    appointments = tuple(appointments)
    if any(appointment.pk is None for appointment in appointments):
        raise ValidationError("La cita debe estar guardada antes de actualizar su estado.")
    appointments = tuple(
        sorted(
            appointments,
            key=lambda appointment: (appointment.business_id, appointment.pk),
        )
    )
    if outcome not in {Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW}:
        raise ValidationError("El resultado elegido no es válido.")

    businesses_by_id = {}
    for appointment in appointments:
        businesses_by_id.setdefault(appointment.business_id, appointment.business)
    for business_id in sorted(businesses_by_id):
        lock_business_calendar(businesses_by_id[business_id])

    for appointment in appointments:
        if outcome == Appointment.Status.COMPLETED:
            complete_appointment(appointment, completed_by=closed_by, at=at)
        else:
            mark_appointment_no_show(appointment, marked_by=closed_by, at=at)
    return len(appointments)


def _locked_appointment_with_calendar(appointment: Appointment) -> Appointment:
    """Apply the global calendar-before-appointment lock order."""
    if appointment.pk is None:
        raise ValidationError("La cita debe estar guardada antes de actualizar su estado.")

    locked_calendar = lock_business_calendar(appointment.business)
    locked_appointment = _locked_appointment(appointment)
    if locked_appointment.business_id != locked_calendar.business.pk:
        raise ValidationError("La cita ya no pertenece a la agenda esperada.")
    return locked_appointment


def _locked_appointment(appointment: Appointment) -> Appointment:
    """Reload and lock the current row before applying an outcome transition."""
    if appointment.pk is None:
        raise ValidationError("La cita debe estar guardada antes de actualizar su estado.")
    return Appointment.objects.select_for_update().get(pk=appointment.pk)


def _appointment_moment(appointment):
    starts_at = timezone.localtime(appointment.starts_at)
    return starts_at.strftime("%d/%m/%Y a las %H:%M")

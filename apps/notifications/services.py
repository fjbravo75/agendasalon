from __future__ import annotations

import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from email.utils import parseaddr
from threading import Event, Thread
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db import close_old_connections, connection, connections, transaction
from django.db.models import Q
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.booking.models import Appointment
from apps.accounts.tokens import professional_email_verification_token_generator
from apps.businesses.models import Business, PlatformActivityEvent, PlatformSettings
from apps.customers.models import BusinessClient, BusinessClientAccess
from apps.core.security_throttle import (
    ThrottleLimit,
    reserve_throttle_attempts,
    throttle_key_digest,
)
from apps.core.features import (
    operational_notification_delivery_enabled,
    operational_notifications_enabled,
)
from apps.customers.services import client_password_fingerprint
from apps.notifications.models import OutboundEmail


CLIENT_EMAIL_TOKEN_SALT = "agendasalon.client-email-verification.v1"
CLIENT_EMAIL_TOKEN_MAX_AGE = 48 * 60 * 60
CLIENT_PASSWORD_RESET_TOKEN_SALT = "agendasalon.client-password-reset.v1"
CLIENT_PASSWORD_RESET_TOKEN_MAX_AGE = 60 * 60
OPERATIONAL_EMAIL_TOKEN_SALT = "agendasalon.operational-email.v1"
OPERATIONAL_EMAIL_TOKEN_MAX_AGE = 48 * 60 * 60
MAX_EMAIL_ATTEMPTS = 3
logger = logging.getLogger(__name__)


_OPERATIONAL_NOTICE_COPY = {
    "verification": (
        "Confirma el correo de avisos",
        "Confirma esta dirección antes de que AgendaSalon envíe avisos operativos.",
    ),
    "test": (
        "Correo de prueba de AgendaSalon",
        "El canal de avisos está configurado y puede recibir mensajes operativos.",
    ),
    "signup_request": (
        "Nueva solicitud de alta",
        "Hay una nueva solicitud de negocio pendiente de revisión.",
    ),
    "business_created": (
        "Nuevo negocio preparado",
        "El negocio y el acceso de su primer profesional ya están preparados.",
    ),
    "professional_activated": (
        "Cuenta profesional activada",
        "Una cuenta profesional ha completado su activación.",
    ),
    "continuity_succeeded": (
        "Continuidad recuperada",
        "La nueva copia ha terminado y todas sus comprobaciones son correctas.",
    ),
    "continuity_failed": (
        "La copia necesita revisión",
        "La última copia no ha terminado correctamente. Revisa Continuidad.",
    ),
    "demo_refresh_requested": (
        "Regeneración solicitada",
        "Se ha registrado una solicitud para reconstruir la demostración.",
    ),
    "demo_refresh_completed": (
        "Regeneración completada",
        "La demostración ha recuperado el escenario canónico de la solicitud.",
    ),
    "demo_refresh_failed": (
        "La regeneración necesita revisión",
        "La reconstrucción no ha podido comprobarse correctamente. Revisa Continuidad.",
    ),
    "email_failure": (
        "Un correo necesita revisión",
        "Un mensaje ha agotado sus intentos. La operación principal permanece guardada.",
    ),
    "new_appointment": (
        "Nueva reserva online",
        "Hay una nueva cita confirmada. Revisa la agenda para consultar sus datos.",
    ),
    "cancellation": (
        "Cita cancelada",
        "Una cita ha dejado de estar confirmada. Revisa la agenda para consultar el cambio.",
    ),
    "client_access": (
        "Cuenta cliente activada",
        "Una persona ha completado el acceso online a su cuenta de cliente.",
    ),
    "holiday_review": (
        "Hay citas que revisar por festivo",
        "La actualización de festivos afecta a citas futuras de este negocio.",
    ),
    "holiday_impact": (
        "La actualización de festivos afecta a citas",
        "Hay citas futuras que necesitan revisión después de actualizar el calendario oficial.",
    ),
}


@dataclass(frozen=True)
class _EmailClaim:
    email_id: int
    lease_token: uuid.UUID
    attempt_number: int
    recovered: bool


class _EmailClaimHeartbeat:
    """Mantiene la reserva mientras el backend SMTP sigue ejecutándose."""

    def __init__(self, claim: _EmailClaim):
        self.claim = claim
        self._stop = Event()
        self._lost = Event()
        self._thread = Thread(
            target=self._run,
            name=f"outbound-email-lease-{claim.email_id}",
            daemon=True,
        )

    @property
    def lost(self):
        return self._lost.is_set()

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join()

    def _run(self):
        close_old_connections()
        try:
            interval = _lease_heartbeat_interval()
            while not self._stop.wait(interval):
                try:
                    renewed = _renew_claim(self.claim)
                except Exception:
                    # Un fallo transitorio se reintenta. Si la reserva llega a
                    # caducar, el siguiente CAS devolverá cero y este worker
                    # ya no podrá cerrar el trabajo de otro.
                    logger.exception(
                        "No se pudo renovar temporalmente la reserva del correo %s.",
                        self.claim.email_id,
                    )
                    continue
                if not renewed:
                    self._lost.set()
                    return
        finally:
            connections.close_all()


def _absolute_url(path: str) -> str:
    return f"{settings.AGENDA_PLATFORM_WEBSITE.rstrip('/')}{path}"


@transaction.atomic
def _upsert_email(*, key, defaults, allow_resend=False):
    email, created = OutboundEmail.objects.get_or_create(
        deduplication_key=key,
        defaults=defaults,
    )
    if created:
        return email

    email = OutboundEmail.objects.select_for_update().get(pk=email.pk)
    lease_is_active = (
        email.status == OutboundEmail.Status.PROCESSING
        and email.lease_expires_at is not None
        and email.lease_expires_at > timezone.now()
    )
    if lease_is_active:
        return email

    if not allow_resend:
        if email.status == OutboundEmail.Status.PENDING:
            refreshed_fields = []
            for field, value in defaults.items():
                if field == "scheduled_for" and email.attempts > 0:
                    continue
                setattr(email, field, value)
                refreshed_fields.append(field)
            if refreshed_fields:
                email.save(update_fields=[*refreshed_fields, "updated_at"])
        return email

    for field, value in defaults.items():
        setattr(email, field, value)
    email.status = OutboundEmail.Status.PENDING
    email.attempts = 0
    email.last_error = ""
    email.sent_at = None
    email.lease_token = None
    email.lease_expires_at = None
    email.delivery_reference = uuid.uuid4()
    email.save()
    return email


def queue_professional_activation(user, *, business=None):
    return _upsert_email(
        key=f"professional-activation:{user.pk}:{user.email_normalized}",
        defaults={
            "kind": OutboundEmail.Kind.PROFESSIONAL_ACTIVATION,
            "business": business,
            "recipient_user": user,
            "recipient_email": user.email,
            "scheduled_for": timezone.now(),
        },
        allow_resend=True,
    )


def queue_professional_email_verification(user, *, business=None):
    return _upsert_email(
        key=f"professional-email:{user.pk}:{user.email_normalized}",
        defaults={
            "kind": OutboundEmail.Kind.PROFESSIONAL_EMAIL_VERIFICATION,
            "business": business,
            "recipient_user": user,
            "recipient_email": user.email,
            "scheduled_for": timezone.now(),
        },
        allow_resend=True,
    )


def queue_client_email_verification(access):
    return _upsert_email(
        key=f"client-email:{access.pk}:{client_password_fingerprint(access)}",
        defaults={
            "kind": OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
            "business": access.business,
            "client_access": access,
            "recipient_email": access.email,
            "scheduled_for": timezone.now(),
        },
        allow_resend=True,
    )


def queue_client_password_reset(access):
    return _upsert_email(
        key=f"client-password-reset:{access.pk}:{client_password_fingerprint(access)}",
        defaults={
            "kind": OutboundEmail.Kind.CLIENT_PASSWORD_RESET,
            "business": access.business,
            "client_access": access,
            "recipient_email": access.email,
            "scheduled_for": timezone.now(),
        },
        allow_resend=True,
    )


def _verified_access_for_appointment(appointment):
    access = appointment.requested_by_client_access
    if access is None:
        access = BusinessClientAccess.objects.filter(
            business=appointment.business,
            business_client=appointment.business_client,
            is_active=True,
            email_verified_at__isnull=False,
        ).first()
    if (
        access is None
        or not access.is_active
        or not access.email_normalized
        or access.email_verified_at is None
    ):
        return None
    return access


def queue_appointment_emails(appointment):
    access = _verified_access_for_appointment(appointment)
    if access is None:
        return ()
    confirmation = _upsert_email(
        key=f"appointment-confirmation:{appointment.pk}:{access.pk}",
        defaults={
            "kind": OutboundEmail.Kind.APPOINTMENT_CONFIRMATION,
            "business": appointment.business,
            "client_access": access,
            "appointment": appointment,
            "recipient_email": access.email,
            "scheduled_for": timezone.now(),
        },
    )
    queued = [confirmation]
    reminder_at = appointment.starts_at - timedelta(hours=24)
    if reminder_at > timezone.now():
        queued.append(
            _upsert_email(
                key=f"appointment-reminder:{appointment.pk}:{access.pk}",
                defaults={
                    "kind": OutboundEmail.Kind.APPOINTMENT_REMINDER,
                    "business": appointment.business,
                    "client_access": access,
                    "appointment": appointment,
                    "recipient_email": access.email,
                    "scheduled_for": reminder_at,
                },
            )
        )
    return tuple(queued)


def operational_email_token(*, scope, target):
    return signing.dumps(
        {
            "scope": scope,
            "target_id": target.pk,
            "email_digest": throttle_key_digest(target.notification_email_normalized),
            "nonce": str(target.notification_email_verification_nonce),
        },
        salt=OPERATIONAL_EMAIL_TOKEN_SALT,
        compress=True,
    )


def operational_email_target_from_token(token, *, scope):
    if scope not in {"platform", "business"}:
        return None
    try:
        payload = signing.loads(
            token,
            salt=OPERATIONAL_EMAIL_TOKEN_SALT,
            max_age=OPERATIONAL_EMAIL_TOKEN_MAX_AGE,
        )
    except signing.BadSignature:
        return None
    if not isinstance(payload, dict) or payload.get("scope") != scope:
        return None
    model = PlatformSettings if scope == "platform" else Business
    target = model.objects.filter(pk=payload.get("target_id")).first()
    if target is None:
        return None
    if (
        not hmac.compare_digest(
            throttle_key_digest(target.notification_email_normalized),
            str(payload.get("email_digest") or ""),
        )
        or str(target.notification_email_verification_nonce) != payload.get("nonce")
        or target.notification_email_verified_at is not None
    ):
        return None
    return target


def verify_operational_email(token, *, scope):
    with transaction.atomic():
        model = PlatformSettings if scope == "platform" else Business
        candidate = operational_email_target_from_token(token, scope=scope)
        if candidate is None:
            return None
        target = model.objects.select_for_update().get(pk=candidate.pk)
        if operational_email_target_from_token(token, scope=scope) is None:
            return None
        if target.notification_email_verified_at is None:
            target.notification_email_verified_at = timezone.now()
            target.save(update_fields=["notification_email_verified_at", "updated_at"])
        return target


def _operational_preference_enabled(target, *, scope, code):
    preferences = {
        ("platform", "continuity_succeeded"): "notify_continuity",
        ("platform", "continuity_failed"): "notify_continuity",
        ("platform", "demo_refresh_requested"): "notify_demo_refresh",
        ("platform", "demo_refresh_completed"): "notify_demo_refresh",
        ("platform", "demo_refresh_failed"): "notify_demo_refresh",
        ("platform", "signup_request"): "notify_signup_requests",
        ("platform", "business_created"): "notify_signup_requests",
        ("platform", "professional_activated"): "notify_signup_requests",
        ("platform", "holiday_impact"): None,
        ("platform", "email_failure"): "notify_email_failures",
        ("business", "new_appointment"): "notify_new_appointments",
        ("business", "cancellation"): "notify_cancellations",
        ("business", "client_access"): "notify_client_access",
        ("business", "holiday_review"): "notify_holiday_reviews",
        ("business", "email_failure"): "notify_email_failures",
        ("platform", "test"): None,
        ("business", "test"): None,
        ("platform", "verification"): None,
        ("business", "verification"): None,
    }
    key = (scope, code)
    if key not in preferences:
        return False
    preference = preferences[key]
    return preference is None or bool(getattr(target, preference, False))


def _operational_capacity_available():
    reservation = reserve_throttle_attempts(
        limits=(
            ThrottleLimit(
                scope="operational_email_global_hour",
                key="global",
                limit=int(settings.AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT),
                window_seconds=60 * 60,
            ),
            ThrottleLimit(
                scope="operational_email_global_day",
                key="global",
                limit=int(settings.AGENDA_OPERATIONAL_EMAIL_DAILY_LIMIT),
                window_seconds=24 * 60 * 60,
            ),
        )
    )
    if not reservation.allowed:
        logger.warning("Pausada la creación de avisos operativos por límite global.")
        try:
            recent_limit_event = PlatformActivityEvent.objects.filter(
                event_type=PlatformActivityEvent.EventType.NOTIFICATION_CAPACITY_REACHED,
                created_at__gte=timezone.now() - timedelta(hours=1),
            ).exists()
            if not recent_limit_event:
                PlatformActivityEvent.objects.create(
                    event_type=(
                        PlatformActivityEvent.EventType.NOTIFICATION_CAPACITY_REACHED
                    ),
                    summary=(
                        "Se alcanzó el límite de seguridad de los avisos por correo. "
                        "La actividad principal permanece guardada."
                    ),
                )
        except Exception:
            logger.exception("No se pudo registrar el límite interno de avisos.")
    return reservation.allowed


def _business_can_receive_operational_notices(business):
    return bool(
        business
        and business.is_active
        and business.memberships.filter(
            is_active=True,
            user__is_active=True,
        ).exists()
    )


def _operational_notice_copy(payload):
    return _OPERATIONAL_NOTICE_COPY.get(
        payload.get("code"),
        (
            "Aviso de AgendaSalon",
            "Hay una situación operativa que necesita revisión en AgendaSalon.",
        ),
    )


def queue_operational_notice(
    *,
    scope,
    code,
    deduplication_key,
    business=None,
    action_path="",
    context=None,
    allow_resend=False,
    require_verified=True,
    require_enabled=True,
):
    if not operational_notification_delivery_enabled():
        return None
    if scope not in {"platform", "business"}:
        raise ValueError("El ámbito del aviso operativo no es válido.")
    if scope == "platform":
        target = PlatformSettings.objects.filter(pk=PlatformSettings.SINGLETON_PK).first()
        if target is None:
            return None
        recipient_user = target.updated_by
        if recipient_user is None:
            recipient_user = get_user_model().objects.filter(is_superuser=True).first()
    else:
        target = business
        recipient_user = None
    if (
        target is None
        or (scope == "business" and not _business_can_receive_operational_notices(target))
        or (require_enabled and not target.notifications_enabled)
        or not target.notification_email_normalized
        or (require_verified and target.notification_email_verified_at is None)
        or not _operational_preference_enabled(target, scope=scope, code=code)
    ):
        return None
    key = f"operational:{scope}:{deduplication_key}"
    if not allow_resend:
        existing = OutboundEmail.objects.filter(deduplication_key=key).first()
        if existing is not None:
            return existing
    if not _operational_capacity_available():
        return None
    return _upsert_email(
        key=key,
        defaults={
            "kind": OutboundEmail.Kind.OPERATIONAL_NOTICE,
            "business": business if scope == "business" else None,
            "recipient_user": recipient_user,
            "recipient_email": target.notification_email,
            "payload": {
                "scope": scope,
                "code": code,
                "action_path": action_path,
                "context": context or {},
            },
            "scheduled_for": timezone.now(),
        },
        allow_resend=allow_resend,
    )


def queue_operational_email_verification(*, scope, target, business=None):
    return queue_operational_notice(
        scope=scope,
        code="verification",
        deduplication_key=(
            f"verification:{target.pk}:{target.notification_email_verification_nonce}"
        ),
        business=business,
        allow_resend=True,
        require_verified=False,
        require_enabled=False,
    )


def queue_operational_email_verification_safely(*, scope, target, business=None):
    """Intenta la verificación sin convertir un fallo de correo en fallo funcional."""

    try:
        email = queue_operational_email_verification(
            scope=scope,
            target=target,
            business=business,
        )
        if email is None:
            return None
        return queue_and_dispatch(email)
    except Exception:
        logger.exception("No se pudo preparar la verificación del correo de avisos.")
        return None


def queue_operational_test(*, scope, target, business=None, action_path):
    return queue_operational_notice(
        scope=scope,
        code="test",
        deduplication_key=f"test:{target.pk}:{uuid.uuid4()}",
        business=business,
        action_path=action_path,
    )


def queue_operational_notice_on_commit(**notice):
    """Encola después del commit sin convertir un fallo de aviso en fallo funcional."""

    def enqueue_and_dispatch():
        try:
            email = queue_operational_notice(**notice)
            if email is not None:
                queue_and_dispatch(email)
        except Exception:
            logger.exception("No se pudo preparar un aviso operativo tras confirmar el hecho.")

    transaction.on_commit(enqueue_and_dispatch)


def mark_operational_email_verified_from_account(user):
    """Reutiliza una verificación personal solo dentro del mismo ámbito."""

    if not user.email_normalized or user.email_verified_at is None:
        return ()
    verified = []
    with transaction.atomic():
        from apps.businesses.activity import record_business_activity
        from apps.businesses.models import BusinessActivityEvent, PlatformActivityEvent

        if user.is_superuser:
            platform = (
                PlatformSettings.objects.select_for_update()
                .filter(
                    pk=PlatformSettings.SINGLETON_PK,
                    notification_email_normalized=user.email_normalized,
                    notification_email_verified_at__isnull=True,
                )
                .first()
            )
            if platform is not None:
                platform.notification_email_verified_at = user.email_verified_at
                platform.save(update_fields=["notification_email_verified_at", "updated_at"])
                PlatformActivityEvent.objects.create(
                    actor_user=user,
                    event_type=PlatformActivityEvent.EventType.NOTIFICATION_EMAIL_VERIFIED,
                    summary="Se reutilizó la verificación del correo personal de la cuenta.",
                )
                verified.append(("platform", platform.pk))
        else:
            businesses = Business.objects.select_for_update().filter(
                memberships__user=user,
                memberships__is_active=True,
                notification_email_normalized=user.email_normalized,
                notification_email_verified_at__isnull=True,
            )
            for business in businesses:
                business.notification_email_verified_at = user.email_verified_at
                business.save(update_fields=["notification_email_verified_at", "updated_at"])
                record_business_activity(
                    business=business,
                    category=BusinessActivityEvent.Category.CONFIGURATION,
                    event_type=BusinessActivityEvent.EventType.NOTIFICATION_SETTINGS_UPDATED,
                    origin=BusinessActivityEvent.Origin.SYSTEM,
                    summary="El correo de avisos reutilizó la verificación de la cuenta.",
                    actor=user,
                    entity=business,
                    entity_type="business",
                    changes={"channel_verified": True, "verification_reused": True},
                )
                verified.append(("business", business.pk))
    return tuple(verified)


def mark_operational_email_verified_from_account_on_commit(user):
    """Aísla la verificación principal de cualquier fallo del canal operativo."""

    user_id = user.pk

    def reuse_verified_email():
        try:
            current_user = get_user_model().objects.get(pk=user_id)
            mark_operational_email_verified_from_account(current_user)
        except Exception:
            logger.exception(
                "No se pudo reutilizar el correo verificado en el canal operativo."
            )

    transaction.on_commit(reuse_verified_email)


def _apply_locked_appointment_email_cancellation(emails, *, now):
    processing_ids = [
        email.pk
        for email in emails
        if email.status == OutboundEmail.Status.PROCESSING
    ]
    pending_ids = [
        email.pk
        for email in emails
        if email.status == OutboundEmail.Status.PENDING
    ]
    if processing_ids:
        OutboundEmail.objects.filter(pk__in=processing_ids).update(
            cancellation_requested_at=now,
            last_error=(
                "La cita se canceló mientras el aviso estaba en curso. "
                "El servicio de correo aún puede aceptarlo."
            ),
            updated_at=now,
        )
    if pending_ids:
        OutboundEmail.objects.filter(pk__in=pending_ids).update(
            status=OutboundEmail.Status.CANCELLED,
            last_error="Cita cancelada.",
            cancellation_requested_at=now,
            lease_token=None,
            lease_expires_at=None,
            updated_at=now,
        )
    return len(emails)


@transaction.atomic
def cancel_appointment_emails(appointment):
    lock_options = {}
    if getattr(connection.features, "has_select_for_update_of", False):
        lock_options["of"] = ("self",)
    emails = list(
        OutboundEmail.objects.select_for_update(**lock_options)
        .filter(
            appointment=appointment,
            status__in=[OutboundEmail.Status.PENDING, OutboundEmail.Status.PROCESSING],
        )
        .order_by("pk")
    )
    return _apply_locked_appointment_email_cancellation(
        emails,
        now=timezone.now(),
    )


def _professional_token_url(user, route_name, *, token_generator=default_token_generator):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = token_generator.make_token(user)
    return _absolute_url(reverse(route_name, args=[uid, token]))


def client_verification_token(access):
    return signing.dumps(
        {
            "access_id": access.pk,
            "email": access.email_normalized,
            "password_fingerprint": client_password_fingerprint(access),
        },
        salt=CLIENT_EMAIL_TOKEN_SALT,
        compress=True,
    )


def client_verification_url(access):
    return _absolute_url(
        reverse(
            "customers:client_email_verify",
            args=[access.business.slug, client_verification_token(access)],
        )
    )


def _client_verification_payload(token):
    try:
        return signing.loads(
            token,
            salt=CLIENT_EMAIL_TOKEN_SALT,
            max_age=CLIENT_EMAIL_TOKEN_MAX_AGE,
        )
    except signing.BadSignature:
        return None


def unverified_client_from_token(token, *, business, lock=False):
    payload = _client_verification_payload(token)
    if not isinstance(payload, dict):
        return None
    queryset = BusinessClientAccess.objects.select_related("business", "business_client")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    access = queryset.filter(
        pk=payload.get("access_id"),
        email_normalized=payload.get("email"),
        business=business,
        is_active=True,
        email_verified_at__isnull=True,
    ).first()
    if (
        access is None
        or (not access.business_client.is_active and not access.is_pending_public_registration)
        or (
            access.is_pending_public_registration
            and (
                access.public_registration_expires_at is None
                or access.public_registration_expires_at <= timezone.now()
            )
        )
        or not hmac.compare_digest(
            str(payload.get("password_fingerprint") or ""),
            client_password_fingerprint(access),
        )
    ):
        return None
    return access


@transaction.atomic
def verified_client_from_token(
    token,
    *,
    business,
    password,
    full_name=None,
    phone=None,
    privacy_acknowledged=False,
    privacy_document=None,
    privacy_legal_context=None,
    privacy_action_fingerprint_source=None,
):
    # La pausa operativa puede cambiar entre el GET y el POST. Bloqueamos y
    # releemos el negocio dentro de la misma transacción antes de consolidar
    # una nueva alta pública.
    locked_business = Business.objects.select_for_update().get(pk=business.pk)
    candidate = unverified_client_from_token(
        token,
        business=locked_business,
    )
    if candidate is None:
        return None
    locked_client = BusinessClient.objects.select_for_update().get(
        pk=candidate.business_client_id,
        business=locked_business,
    )
    access = unverified_client_from_token(
        token,
        business=locked_business,
        lock=True,
    )
    if access is None or access.business_client_id != locked_client.pk:
        return None
    if (
        access.is_pending_public_registration
        and not locked_business.accepts_public_bookings()
    ):
        raise ValidationError(
            "Las altas online están pausadas. No hemos activado tu cuenta; "
            "podrás terminarla cuando el negocio vuelva a admitir reservas."
        )
    if locked_business.legal_compliance_enabled and not privacy_acknowledged:
        raise ValidationError("Debes confirmar la información de privacidad vigente.")

    was_pending_public_registration = access.is_pending_public_registration
    client_update_fields = []
    if was_pending_public_registration:
        locked_client.is_active = True
        client_update_fields.append("is_active")
        if full_name is not None:
            locked_client.full_name = full_name.strip()
            client_update_fields.extend(["full_name", "full_name_normalized"])
        if phone is not None:
            locked_client.phone = phone.strip()
            access.phone = phone.strip()
            client_update_fields.extend(["phone", "phone_normalized"])
    if (locked_client.email or "").strip() != (access.email or "").strip():
        locked_client.email = access.email
        client_update_fields.append("email")
    if client_update_fields:
        locked_client.full_clean()
        locked_client.save(update_fields=[*client_update_fields, "updated_at"])
    access.set_password(password)
    access.email_verified_at = timezone.now()
    access.is_pending_public_registration = False
    access.public_registration_expires_at = None
    access_update_fields = [
        "password_hash",
        "email_verified_at",
        "is_pending_public_registration",
        "public_registration_expires_at",
        "updated_at",
    ]
    if was_pending_public_registration and phone is not None:
        access_update_fields.extend(["phone", "phone_normalized"])
    access.full_clean()
    access.save(
        update_fields=access_update_fields
    )
    if locked_business.legal_compliance_enabled:
        from apps.legal.models import LegalAcceptance
        from apps.legal.services import acknowledge_customer_privacy

        context = (
            LegalAcceptance.Context.CLIENT_REGISTRATION
            if locked_client.source == "other"
            else LegalAcceptance.Context.CLIENT_INVITATION
        )
        acknowledge_customer_privacy(
            client_access=access,
            context=context,
            document=privacy_document,
            legal_context_snapshot=privacy_legal_context,
            action_fingerprint_source=privacy_action_fingerprint_source,
        )
    queue_operational_notice_on_commit(
        scope="business",
        code="client_access",
        deduplication_key=(
            f"client-access:{access.pk}:{throttle_key_digest(access.email_normalized)}"
        ),
        business=locked_business,
        action_path=reverse(
            "customers:professional_client_detail",
            args=[locked_client.pk],
        ),
    )
    return access


def client_password_reset_token(access):
    return signing.dumps(
        {
            "access_id": access.pk,
            "business_id": access.business_id,
            "email": access.email_normalized,
            "password_fingerprint": client_password_fingerprint(access),
        },
        salt=CLIENT_PASSWORD_RESET_TOKEN_SALT,
        compress=True,
    )


def client_password_reset_url(access):
    return _absolute_url(
        reverse(
            "customers:client_password_reset",
            args=[access.business.slug, client_password_reset_token(access)],
        )
    )


def _client_password_reset_payload(token):
    try:
        return signing.loads(
            token,
            salt=CLIENT_PASSWORD_RESET_TOKEN_SALT,
            max_age=CLIENT_PASSWORD_RESET_TOKEN_MAX_AGE,
        )
    except signing.BadSignature:
        return None


def client_password_reset_access_from_token(token, *, business, lock=False):
    payload = _client_password_reset_payload(token)
    if not isinstance(payload, dict) or payload.get("business_id") != business.pk:
        return None
    queryset = BusinessClientAccess.objects.select_related("business", "business_client")
    if lock:
        # El negocio y la ficha se bloquean antes en el servicio de reset. En
        # PostgreSQL limitamos este último FOR UPDATE al acceso para conservar
        # de forma explícita el orden negocio -> ficha -> acceso.
        queryset = queryset.select_for_update(of=("self",))
    access = queryset.filter(
        pk=payload.get("access_id"),
        business=business,
        email_normalized=payload.get("email"),
        is_active=True,
        email_verified_at__isnull=False,
        business_client__is_active=True,
    ).first()
    if access is None:
        return None
    if payload.get("password_fingerprint") != client_password_fingerprint(access):
        return None
    return access


@transaction.atomic
def reset_client_password_from_token(token, *, business, password):
    locked_business = Business.objects.select_for_update().filter(pk=business.pk).first()
    if locked_business is None:
        return None
    candidate = client_password_reset_access_from_token(
        token,
        business=locked_business,
    )
    if candidate is None:
        return None
    locked_client = (
        BusinessClient.objects.select_for_update()
        .filter(
            pk=candidate.business_client_id,
            business=locked_business,
            is_active=True,
        )
        .first()
    )
    if locked_client is None:
        return None
    access = client_password_reset_access_from_token(
        token,
        business=locked_business,
        lock=True,
    )
    if access is None or access.business_client_id != locked_client.pk:
        return None
    access.set_password(password)
    access.save(update_fields=["password_hash", "updated_at"])
    return access


def _delivery_context(email):
    context = {"email": email, "business": email.business}
    if email.kind == OutboundEmail.Kind.OPERATIONAL_NOTICE:
        title, detail = _operational_notice_copy(email.payload)
        action_path = email.payload.get("action_path") or ""
        if email.payload.get("code") == "verification":
            scope = email.payload.get("scope")
            target = (
                PlatformSettings.objects.filter(pk=PlatformSettings.SINGLETON_PK).first()
                if scope == "platform"
                else email.business
            )
            if target is not None:
                route_name = (
                    "notifications:platform_email_verify"
                    if scope == "platform"
                    else "notifications:business_email_verify"
                )
                action_path = reverse(
                    route_name,
                    args=[operational_email_token(scope=scope, target=target)],
                )
        context.update(
            notice={"title": title, "detail": detail},
            action_url=_absolute_url(action_path) if action_path else "",
            action_label=(
                "Confirmar correo"
                if email.payload.get("code") == "verification"
                else "Revisar en AgendaSalon"
            ),
        )
    elif email.kind == OutboundEmail.Kind.PROFESSIONAL_ACTIVATION:
        context.update(
            user=email.recipient_user,
            action_url=_professional_token_url(
                email.recipient_user,
                "accounts:professional_activate",
            ),
        )
    elif email.kind == OutboundEmail.Kind.PROFESSIONAL_EMAIL_VERIFICATION:
        context.update(
            user=email.recipient_user,
            action_url=_professional_token_url(
                email.recipient_user,
                "accounts:professional_email_verify",
                token_generator=professional_email_verification_token_generator,
            ),
        )
    elif email.kind == OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION:
        context.update(
            access=email.client_access,
            client=email.client_access.business_client,
            action_url=client_verification_url(email.client_access),
        )
    elif email.kind == OutboundEmail.Kind.CLIENT_PASSWORD_RESET:
        context.update(
            access=email.client_access,
            client=email.client_access.business_client,
            action_url=client_password_reset_url(email.client_access),
        )
    else:
        appointment = email.appointment
        context.update(
            access=email.client_access,
            client=appointment.business_client,
            appointment=appointment,
            starts_at=timezone.localtime(appointment.starts_at),
        )
    return context


def _is_still_valid(email):
    if email.kind == OutboundEmail.Kind.OPERATIONAL_NOTICE:
        if not operational_notifications_enabled():
            return False
        scope = email.payload.get("scope")
        if scope not in {"platform", "business"}:
            return False
        target = (
            PlatformSettings.objects.filter(pk=PlatformSettings.SINGLETON_PK).first()
            if scope == "platform"
            else email.business
        )
        if target is None or target.notification_email_normalized != email.recipient_email.lower():
            return False
        if scope == "business" and not _business_can_receive_operational_notices(target):
            return False
        code = email.payload.get("code")
        if code == "verification":
            expected_key = (
                f"operational:{scope}:verification:{target.pk}:"
                f"{target.notification_email_verification_nonce}"
            )
            return bool(
                target.notification_email_verified_at is None
                and email.deduplication_key == expected_key
            )
        return bool(
            target.notifications_enabled
            and target.notification_email_verified_at is not None
            and _operational_preference_enabled(target, scope=scope, code=code)
        )
    if email.kind == OutboundEmail.Kind.PROFESSIONAL_ACTIVATION:
        user = email.recipient_user
        return bool(user and not user.is_active and user.email_normalized == email.recipient_email.lower())
    if email.kind == OutboundEmail.Kind.PROFESSIONAL_EMAIL_VERIFICATION:
        user = email.recipient_user
        return bool(
            user
            and user.is_active
            and user.email_verified_at is None
            and user.email_normalized == email.recipient_email.lower()
        )
    if email.kind == OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION:
        access = email.client_access
        return bool(
            access
            and access.is_active
            and (
                access.business_client.is_active
                or (
                    access.is_pending_public_registration
                    and access.public_registration_expires_at is not None
                    and access.public_registration_expires_at > timezone.now()
                )
            )
            and access.email_verified_at is None
            and access.email_normalized == email.recipient_email.lower()
            and email.deduplication_key
            == f"client-email:{access.pk}:{client_password_fingerprint(access)}"
        )
    if email.kind == OutboundEmail.Kind.CLIENT_PASSWORD_RESET:
        access = email.client_access
        return bool(
            access
            and access.is_active
            and access.business_client.is_active
            and access.email_verified_at is not None
            and access.email_normalized == email.recipient_email.lower()
            and email.deduplication_key
            == f"client-password-reset:{access.pk}:{client_password_fingerprint(access)}"
        )
    appointment = email.appointment
    access = email.client_access
    return bool(
        appointment
        and appointment.status == Appointment.Status.CONFIRMED
        and access
        and access.is_active
        and access.email_verified_at is not None
        and access.email_normalized == email.recipient_email.lower()
        and (
            email.kind != OutboundEmail.Kind.APPOINTMENT_REMINDER
            or appointment.starts_at > timezone.now()
        )
    )


def _lease_duration():
    seconds = int(settings.AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS)
    if seconds <= 0:
        raise ValueError("AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS debe ser mayor que cero.")
    return timedelta(seconds=seconds)


def _lease_heartbeat_interval():
    return max(
        0.1,
        min(30.0, int(settings.AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS) / 3),
    )


def _claim_lock_queryset():
    options = {}
    if getattr(connection.features, "has_select_for_update_skip_locked", False):
        options["skip_locked"] = True
    if getattr(connection.features, "has_select_for_update_of", False):
        options["of"] = ("self",)
    return OutboundEmail.objects.select_for_update(**options)


def _claimable_email_filter(now):
    return Q(
        status=OutboundEmail.Status.PENDING,
        scheduled_for__lte=now,
    ) | (
        Q(status=OutboundEmail.Status.PROCESSING)
        & (Q(lease_expires_at__lte=now) | Q(lease_expires_at__isnull=True))
    )


def _claim_outbound_email(*, email_id=None):
    now = timezone.now()
    with transaction.atomic():
        queryset = _claim_lock_queryset().filter(_claimable_email_filter(now))
        if email_id is not None:
            queryset = queryset.filter(pk=email_id)
        email = queryset.order_by("scheduled_for", "pk").first()
        if email is None:
            return None, None

        recovered = email.status == OutboundEmail.Status.PROCESSING
        if email.attempts >= MAX_EMAIL_ATTEMPTS:
            email.status = OutboundEmail.Status.FAILED
            email.last_error = (
                "El envío anterior quedó interrumpido y ya había agotado sus intentos."
            )
            email.lease_token = None
            email.lease_expires_at = None
            email.save(
                update_fields=[
                    "status",
                    "last_error",
                    "lease_token",
                    "lease_expires_at",
                    "updated_at",
                ]
            )
            logger.warning(
                "El correo transaccional %s agotó sus intentos tras una reserva interrumpida.",
                email.pk,
            )
            return None, email.pk

        lease_token = uuid.uuid4()
        email.status = OutboundEmail.Status.PROCESSING
        email.attempts += 1
        email.lease_token = lease_token
        email.lease_expires_at = now + _lease_duration()
        email.save(
            update_fields=[
                "status",
                "attempts",
                "lease_token",
                "lease_expires_at",
                "updated_at",
            ]
        )

    if recovered:
        logger.warning(
            "Recuperada la reserva caducada del correo transaccional %s en el intento %s.",
            email.pk,
            email.attempts,
        )
    return (
        _EmailClaim(
            email_id=email.pk,
            lease_token=lease_token,
            attempt_number=email.attempts,
            recovered=recovered,
        ),
        None,
    )


def _active_claim_email(claim):
    return (
        OutboundEmail.objects.select_related(
            "business",
            "recipient_user",
            "client_access__business_client",
            "appointment__business_client",
        )
        .filter(
            pk=claim.email_id,
            status=OutboundEmail.Status.PROCESSING,
            lease_token=claim.lease_token,
            lease_expires_at__gt=timezone.now(),
        )
        .first()
    )


def _renew_claim(claim):
    renewed_at = timezone.now()
    renewed_until = renewed_at + _lease_duration()
    renewed = OutboundEmail.objects.filter(
        pk=claim.email_id,
        status=OutboundEmail.Status.PROCESSING,
        lease_token=claim.lease_token,
        lease_expires_at__gt=renewed_at,
    ).update(
        lease_expires_at=renewed_until,
        updated_at=renewed_at,
    )
    return renewed == 1


def _current_email(email_id):
    return OutboundEmail.objects.get(pk=email_id)


def _cancel_claim(claim, *, reason=None):
    OutboundEmail.objects.filter(
        pk=claim.email_id,
        status=OutboundEmail.Status.PROCESSING,
        lease_token=claim.lease_token,
    ).update(
        status=OutboundEmail.Status.CANCELLED,
        last_error=(reason or "El destinatario o la operación ya no están vigentes."),
        lease_token=None,
        lease_expires_at=None,
        updated_at=timezone.now(),
    )
    return _current_email(claim.email_id)


def _finish_claim_with_error(claim, exc):
    email = _active_claim_email(claim)
    if email is None:
        return _current_email(claim.email_id)
    if email.cancellation_requested_at is not None:
        return _cancel_claim(claim, reason="Cita cancelada durante el envío.")
    if not _is_still_valid(email):
        return _cancel_claim(claim)

    next_status = (
        OutboundEmail.Status.FAILED
        if claim.attempt_number >= MAX_EMAIL_ATTEMPTS
        else OutboundEmail.Status.PENDING
    )
    OutboundEmail.objects.filter(
        pk=claim.email_id,
        status=OutboundEmail.Status.PROCESSING,
        lease_token=claim.lease_token,
        cancellation_requested_at__isnull=True,
    ).update(
        status=next_status,
        last_error=str(exc)[:500],
        scheduled_for=timezone.now() + timedelta(minutes=5 * claim.attempt_number),
        lease_token=None,
        lease_expires_at=None,
        updated_at=timezone.now(),
    )
    current = _current_email(claim.email_id)
    if (
        current.status == OutboundEmail.Status.PROCESSING
        and current.lease_token == claim.lease_token
        and current.cancellation_requested_at is not None
    ):
        return _cancel_claim(claim, reason="Cita cancelada durante el envío.")
    return current


def _finish_claim_as_accepted(claim):
    accepted_at = timezone.now()
    OutboundEmail.objects.filter(
        pk=claim.email_id,
        status=OutboundEmail.Status.PROCESSING,
        lease_token=claim.lease_token,
    ).update(
        status=OutboundEmail.Status.SENT,
        sent_at=accepted_at,
        last_error="",
        lease_token=None,
        lease_expires_at=None,
        updated_at=accepted_at,
    )
    return _current_email(claim.email_id)


def _message_id_domain():
    sender_domain = parseaddr(settings.DEFAULT_FROM_EMAIL)[1].rpartition("@")[2]
    website_domain = urlparse(settings.AGENDA_PLATFORM_WEBSITE).hostname
    return (sender_domain or website_domain or "agendasalon.local").encode("idna").decode("ascii")


def _outbound_email_block_reason():
    if getattr(settings, "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL", False):
        return "El correo saliente está suprimido durante la regeneración de la demo."
    if not getattr(settings, "AGENDA_TRANSACTIONAL_EMAIL_ENABLED", False):
        return "El correo transaccional no está activado en este entorno."
    return ""


def _dispatch_claim(claim):
    email = _active_claim_email(claim)
    if email is None:
        return _current_email(claim.email_id)
    if email.cancellation_requested_at is not None:
        return _cancel_claim(claim, reason="Cita cancelada antes del envío.")
    if not _is_still_valid(email):
        return _cancel_claim(claim)

    try:
        block_reason = _outbound_email_block_reason()
        if block_reason:
            raise RuntimeError(block_reason)
        context = _delivery_context(email)
        subject = render_to_string(
            f"emails/{email.kind}_subject.txt",
            context,
        ).strip().replace("\n", " ")
        text_body = render_to_string(f"emails/{email.kind}.txt", context)
        html_body = render_to_string(f"emails/{email.kind}.html", context)
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email.recipient_email],
            headers={
                # La referencia se conserva en reintentos automáticos para
                # correlacionarlos. SMTP no garantiza una entrega exactamente una vez.
                "Message-ID": f"<{email.delivery_reference}@{_message_id_domain()}>",
                "X-AgendaSalon-Delivery-Reference": str(email.delivery_reference),
            },
        )
        message.attach_alternative(html_body, "text/html")
        if not _renew_claim(claim):
            return _current_email(claim.email_id)
        email = _active_claim_email(claim)
        if email is None:
            return _current_email(claim.email_id)
        if email.cancellation_requested_at is not None:
            return _cancel_claim(claim, reason="Cita cancelada antes del envío.")
        if not _is_still_valid(email):
            return _cancel_claim(claim)
        heartbeat = _EmailClaimHeartbeat(claim)
        heartbeat.start()
        try:
            accepted_count = message.send(fail_silently=False)
        finally:
            heartbeat.stop()
        if heartbeat.lost:
            logger.warning(
                "El correo transaccional %s perdió su reserva durante la llamada SMTP.",
                claim.email_id,
            )
            return _current_email(claim.email_id)
        if accepted_count != 1:
            raise RuntimeError("El servicio de correo no confirmó la aceptación del mensaje.")
    except Exception as exc:  # El error queda trazado para reintentos operativos.
        return _finish_claim_with_error(claim, exc)

    return _finish_claim_as_accepted(claim)


def _queue_failure_alerts(email):
    if email.status != OutboundEmail.Status.FAILED:
        return
    if (
        email.kind == OutboundEmail.Kind.OPERATIONAL_NOTICE
        and email.payload.get("code") == "email_failure"
    ):
        return
    try:
        queue_operational_notice(
            scope="platform",
            code="email_failure",
            deduplication_key=f"email-failure:{email.pk}:platform",
            action_path=reverse("notifications:superadmin_notifications"),
        )
        if email.business_id:
            queue_operational_notice(
                scope="business",
                code="email_failure",
                deduplication_key=f"email-failure:{email.pk}:business",
                business=email.business,
                action_path=reverse("business_settings:professional_settings"),
            )
    except Exception:
        logger.exception("No se pudo registrar el aviso de un correo fallido.")


def dispatch_outbound_email(email_id):
    if _outbound_email_block_reason():
        return _current_email(email_id)
    claim, terminal_email_id = _claim_outbound_email(email_id=email_id)
    if claim is None:
        email = _current_email(terminal_email_id or email_id)
    else:
        email = _dispatch_claim(claim)
    _queue_failure_alerts(email)
    return email


def dispatch_due_emails(*, limit=100):
    if _outbound_email_block_reason():
        return []
    delivered = []
    for _ in range(max(0, limit)):
        claim, terminal_email_id = _claim_outbound_email()
        if claim is None and terminal_email_id is None:
            break
        if claim is None:
            email = _current_email(terminal_email_id)
            _queue_failure_alerts(email)
            delivered.append(email)
            continue
        email = _dispatch_claim(claim)
        _queue_failure_alerts(email)
        delivered.append(email)
    return delivered


def queue_and_dispatch(email):
    """Intenta el envío inmediato; un fallo queda en cola y no rompe la operación."""

    return dispatch_outbound_email(email.pk)

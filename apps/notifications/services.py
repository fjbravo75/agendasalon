from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import signing
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.booking.models import Appointment
from apps.customers.models import BusinessClientAccess
from apps.notifications.models import OutboundEmail


CLIENT_EMAIL_TOKEN_SALT = "agendasalon.client-email-verification.v1"
CLIENT_EMAIL_TOKEN_MAX_AGE = 48 * 60 * 60
MAX_EMAIL_ATTEMPTS = 3


def _absolute_url(path: str) -> str:
    return f"{settings.AGENDA_PLATFORM_WEBSITE.rstrip('/')}{path}"


def _upsert_email(*, key, defaults, allow_resend=False):
    email, created = OutboundEmail.objects.get_or_create(
        deduplication_key=key,
        defaults=defaults,
    )
    if not created and (email.status != OutboundEmail.Status.SENT or allow_resend):
        for field, value in defaults.items():
            setattr(email, field, value)
        email.status = OutboundEmail.Status.PENDING
        email.attempts = 0
        email.last_error = ""
        email.sent_at = None
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
        key=f"client-email:{access.pk}:{access.email_normalized}",
        defaults={
            "kind": OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
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


def cancel_appointment_emails(appointment):
    return OutboundEmail.objects.filter(
        appointment=appointment,
        status__in=[OutboundEmail.Status.PENDING, OutboundEmail.Status.PROCESSING],
    ).update(status=OutboundEmail.Status.CANCELLED, last_error="Cita cancelada.")


def _professional_token_url(user, route_name):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return _absolute_url(reverse(route_name, args=[uid, token]))


def client_verification_token(access):
    return signing.dumps(
        {"access_id": access.pk, "email": access.email_normalized},
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


def verified_client_from_token(token, *, business):
    try:
        payload = signing.loads(
            token,
            salt=CLIENT_EMAIL_TOKEN_SALT,
            max_age=CLIENT_EMAIL_TOKEN_MAX_AGE,
        )
    except signing.BadSignature:
        return None
    access = BusinessClientAccess.objects.select_related("business", "business_client").filter(
        pk=payload.get("access_id"),
        email_normalized=payload.get("email"),
        business=business,
        is_active=True,
    ).first()
    if access is None or access.email_verified_at is not None:
        return None
    access.email_verified_at = timezone.now()
    access.save(update_fields=["email_verified_at", "updated_at"])
    return access


def _delivery_context(email):
    context = {"email": email, "business": email.business}
    if email.kind == OutboundEmail.Kind.PROFESSIONAL_ACTIVATION:
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
            ),
        )
    elif email.kind == OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION:
        context.update(
            access=email.client_access,
            client=email.client_access.business_client,
            action_url=client_verification_url(email.client_access),
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
            and access.email_verified_at is None
            and access.email_normalized == email.recipient_email.lower()
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


def dispatch_outbound_email(email_id):
    with transaction.atomic():
        email = OutboundEmail.objects.select_for_update(of=("self",)).select_related(
            "business",
            "recipient_user",
            "client_access__business_client",
            "appointment__business_client",
        ).get(pk=email_id)
        if email.status in {OutboundEmail.Status.SENT, OutboundEmail.Status.CANCELLED}:
            return email
        if email.scheduled_for > timezone.now():
            return email
        if not _is_still_valid(email):
            email.status = OutboundEmail.Status.CANCELLED
            email.last_error = "El destinatario o la operacion ya no estan vigentes."
            email.save(update_fields=["status", "last_error", "updated_at"])
            return email
        email.status = OutboundEmail.Status.PROCESSING
        email.attempts += 1
        email.save(update_fields=["status", "attempts", "updated_at"])

    try:
        if not settings.AGENDA_TRANSACTIONAL_EMAIL_ENABLED:
            raise RuntimeError("El correo transaccional no está activado en este entorno.")
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
        )
        message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)
    except Exception as exc:  # El error queda trazado para reintentos operativos.
        email.refresh_from_db()
        email.status = (
            OutboundEmail.Status.FAILED
            if email.attempts >= MAX_EMAIL_ATTEMPTS
            else OutboundEmail.Status.PENDING
        )
        email.last_error = str(exc)[:500]
        email.scheduled_for = timezone.now() + timedelta(minutes=5 * email.attempts)
        email.save(update_fields=["status", "last_error", "scheduled_for", "updated_at"])
        return email

    email.refresh_from_db()
    email.status = OutboundEmail.Status.SENT
    email.sent_at = timezone.now()
    email.last_error = ""
    email.save(update_fields=["status", "sent_at", "last_error", "updated_at"])
    return email


def dispatch_due_emails(*, limit=100):
    email_ids = list(
        OutboundEmail.objects.filter(
            status=OutboundEmail.Status.PENDING,
            scheduled_for__lte=timezone.now(),
        )
        .order_by("scheduled_for", "pk")
        .values_list("pk", flat=True)[:limit]
    )
    return [dispatch_outbound_email(email_id) for email_id in email_ids]


def queue_and_dispatch(email):
    """Intenta el envío inmediato; un fallo queda en cola y no rompe la operación."""

    return dispatch_outbound_email(email.pk)

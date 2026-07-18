import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class InternalNotification(models.Model):
    """Internal or simulated notification. No real message is sent."""

    class Channel(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        SMS = "sms", "SMS"
        EMAIL = "email", "Email"
        INTERNAL = "internal", "Interna"

    class EventType(models.TextChoices):
        APPOINTMENT_CONFIRMED = "appointment_confirmed", "Cita confirmada"
        APPOINTMENT_CANCELLED = "appointment_cancelled", "Cita cancelada"
        INTERNAL_REMINDER = "internal_reminder", "Recordatorio interno"

    class Status(models.TextChoices):
        REGISTERED = "registrada", "Registrada"
        SIMULATED = "simulada", "Simulada"
        READ = "leida", "Leida"
        DISCARDED = "descartada", "Descartada"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name="negocio",
    )
    business_client = models.ForeignKey(
        "customers.BusinessClient",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="ficha de cliente",
    )
    appointment = models.ForeignKey(
        "booking.Appointment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="cita",
    )
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="internal_notifications",
        verbose_name="usuario destinatario",
    )
    channel = models.CharField("canal", max_length=20, choices=Channel.choices)
    event_type = models.CharField("evento", max_length=40, choices=EventType.choices)
    content = models.TextField("contenido")
    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.REGISTERED,
    )
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    read_at = models.DateTimeField("fecha de lectura", null=True, blank=True)

    class Meta:
        verbose_name = "notificacion interna"
        verbose_name_plural = "notificaciones internas"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["business", "status"], name="notif_business_status_idx"),
            models.Index(fields=["event_type"], name="notification_event_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.business_client_id and self.business_client.business_id != self.business_id:
            errors["business_client"] = "La ficha debe pertenecer al mismo negocio."
        if self.appointment_id and self.appointment.business_id != self.business_id:
            errors["appointment"] = "La cita debe pertenecer al mismo negocio."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.get_event_type_display()} - {self.business}"


class OutboundEmail(models.Model):
    """Cola persistente de correos transaccionales, sin guardar tokens ni cuerpos."""

    class Kind(models.TextChoices):
        PROFESSIONAL_ACTIVATION = "professional_activation", "Activación profesional"
        PROFESSIONAL_EMAIL_VERIFICATION = (
            "professional_email_verification",
            "Verificación profesional",
        )
        CLIENT_EMAIL_VERIFICATION = "client_email_verification", "Verificación cliente"
        CLIENT_PASSWORD_RESET = "client_password_reset", "Recuperación de contraseña cliente"
        APPOINTMENT_CONFIRMATION = "appointment_confirmation", "Confirmación de cita"
        APPOINTMENT_REMINDER = "appointment_reminder", "Recordatorio de cita"
        OPERATIONAL_NOTICE = "operational_notice", "Aviso operativo"

    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PROCESSING = "processing", "Procesando"
        SENT = "sent", "Enviado"
        FAILED = "failed", "Fallido"
        CANCELLED = "cancelled", "Cancelado"

    kind = models.CharField("tipo", max_length=48, choices=Kind.choices)
    status = models.CharField(
        "estado",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outbound_emails",
    )
    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outbound_emails",
    )
    client_access = models.ForeignKey(
        "customers.BusinessClientAccess",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outbound_emails",
    )
    appointment = models.ForeignKey(
        "booking.Appointment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="outbound_emails",
    )
    recipient_email = models.EmailField("destinatario")
    payload = models.JSONField("contenido operativo", default=dict, blank=True)
    deduplication_key = models.CharField(max_length=255, unique=True)
    delivery_reference = models.UUIDField(
        "identificador del aviso",
        default=uuid.uuid4,
        editable=False,
        db_index=True,
    )
    scheduled_for = models.DateTimeField("programado para", default=timezone.now, db_index=True)
    attempts = models.PositiveSmallIntegerField("intentos", default=0)
    lease_token = models.UUIDField(
        "identificador del procesamiento",
        null=True,
        blank=True,
        editable=False,
    )
    lease_expires_at = models.DateTimeField(
        "procesamiento reservado hasta",
        null=True,
        blank=True,
        editable=False,
    )
    cancellation_requested_at = models.DateTimeField(
        "cancelación solicitada el",
        null=True,
        blank=True,
        editable=False,
    )
    sent_at = models.DateTimeField("enviado el", null=True, blank=True)
    last_error = models.CharField("último error", max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_for", "pk"]
        indexes = [
            models.Index(fields=["status", "scheduled_for"], name="email_status_schedule_idx"),
            models.Index(fields=["status", "lease_expires_at"], name="email_status_lease_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        status="processing",
                        lease_token__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    | (
                        ~Q(status="processing")
                        & Q(lease_token__isnull=True)
                        & Q(lease_expires_at__isnull=True)
                    )
                ),
                name="email_processing_lease_state",
            )
        ]

    def __str__(self):
        return f"{self.get_kind_display()} -> {self.recipient_email}"

    @property
    def operational_status_label(self):
        if self.status == self.Status.SENT:
            return "Aceptado por el servicio de correo"
        return self.get_status_display()

    @property
    def operational_status_message(self):
        if self.status == self.Status.SENT:
            if self.cancellation_requested_at is not None:
                return (
                    "El servicio de correo aceptó el aviso cuando la cancelación "
                    "de la cita ya estaba en curso. El mensaje todavía puede llegar "
                    "a la bandeja del cliente."
                )
            return (
                "El servicio de correo aceptó el aviso. "
                "Esto no confirma que haya llegado a su bandeja ni que lo haya leído."
            )
        if self.status == self.Status.PENDING:
            if self.attempts:
                return (
                    "El aviso todavía no ha sido aceptado por el servicio de correo. "
                    "Se volverá a intentar automáticamente."
                )
            return "El aviso está pendiente de su primer intento."
        if self.status == self.Status.PROCESSING:
            if self.cancellation_requested_at is not None:
                return (
                    "La cita está cancelada, pero el aviso ya estaba en curso. "
                    "El servicio de correo todavía puede aceptarlo."
                )
            return "El aviso se está procesando en este momento."
        if self.status == self.Status.FAILED:
            return (
                "El servicio de correo no aceptó el aviso tras agotar los intentos. "
                "Revisa la dirección del cliente y avisa al administrador de AgendaSalon "
                "si el problema continúa."
            )
        if self.status == self.Status.CANCELLED:
            return "El aviso se canceló y no volverá a intentarse."
        return "Consulta el estado operativo de este aviso."

# Create your models here.

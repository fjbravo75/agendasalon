from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


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

# Create your models here.

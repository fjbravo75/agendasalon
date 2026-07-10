from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models


def business_public_image_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"businesses/{instance.slug}/public-{uuid4().hex}{extension}"


class Business(models.Model):
    """Business subscribed to AgendaSalon."""

    class ProfessionalTheme(models.TextChoices):
        LIGHT = "light", "Modo claro"
        DARK = "dark", "Modo oscuro"

    commercial_name = models.CharField("nombre comercial", max_length=160)
    slug = models.SlugField("slug", max_length=180, unique=True)
    public_description = models.TextField("descripción pública", blank=True)
    public_phone = models.CharField("teléfono público", max_length=32, blank=True)
    public_email = models.EmailField("correo público", blank=True)
    address = models.CharField("dirección", max_length=255, blank=True)
    city = models.CharField("localidad", max_length=120, blank=True)
    province = models.CharField("provincia", max_length=120, blank=True)
    is_active = models.BooleanField("activo", default=True)
    public_booking_enabled = models.BooleanField("reserva pública activa", default=True)
    professional_theme = models.CharField(
        "tema del panel profesional",
        max_length=12,
        choices=ProfessionalTheme.choices,
        default=ProfessionalTheme.LIGHT,
    )
    public_image = models.ImageField(
        "imagen pública personalizada",
        upload_to=business_public_image_upload_to,
        blank=True,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )
    last_activity_at = models.DateTimeField("última actividad", null=True, blank=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "negocio"
        verbose_name_plural = "negocios"
        ordering = ["commercial_name"]
        indexes = [
            models.Index(fields=["is_active"], name="business_active_idx"),
            models.Index(fields=["slug"], name="business_slug_idx"),
        ]

    def __str__(self):
        return self.commercial_name

    def is_operational_for_agenda(self):
        """Return whether the business has the minimum setup for appointments."""
        if not self.is_active:
            return False
        return (
            self.services.filter(is_active=True).exists()
            and self.availability_rules.filter(is_active=True).exists()
            and self.work_lines.filter(is_active=True).exists()
        )

    def accepts_public_bookings(self):
        return self.is_active and self.public_booking_enabled


class BusinessMembership(models.Model):
    """Active professional access from a user to a business."""

    class Role(models.TextChoices):
        PROFESSIONAL_ADMIN = "professional_admin", "Administrador profesional"

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="negocio",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="business_memberships",
        verbose_name="usuario",
    )
    role = models.CharField(
        "rol",
        max_length=40,
        choices=Role.choices,
        default=Role.PROFESSIONAL_ADMIN,
    )
    is_active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "pertenencia profesional"
        verbose_name_plural = "pertenencias profesionales"
        ordering = ["business__commercial_name", "user__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "user"],
                name="unique_business_membership",
            )
        ]
        indexes = [
            models.Index(fields=["business", "is_active"], name="membership_business_active_idx"),
            models.Index(fields=["user", "is_active"], name="membership_user_active_idx"),
        ]

    def __str__(self):
        return f"{self.user} en {self.business}"


class BusinessActivityEvent(models.Model):
    """Append-only operational activity scoped to one business."""

    class Category(models.TextChoices):
        APPOINTMENTS = "appointments", "Citas"
        CONFIGURATION = "configuration", "Configuración"
        ACCESS = "access", "Accesos"
        PLATFORM = "platform", "Plataforma"

    class ActorType(models.TextChoices):
        PROFESSIONAL = "professional", "Profesional"
        CUSTOMER = "customer", "Cliente"
        SUPERADMIN = "superadmin", "Superadministrador"
        SYSTEM = "system", "Sistema"

    class Origin(models.TextChoices):
        PHONE = "telefono", "Teléfono"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Correo electrónico"
        FRONT_DESK = "mostrador", "Mostrador"
        PUBLIC_WEB = "web_publica", "Reserva online"
        PROFESSIONAL_PANEL = "panel_profesional", "Panel profesional"
        PLATFORM = "plataforma", "Administración de la plataforma"
        SYSTEM = "sistema", "Sistema"
        OTHER = "otro", "Otro"

    class EventType(models.TextChoices):
        APPOINTMENT_CREATED = "appointment_created", "Cita creada"
        APPOINTMENT_CANCELLED = "appointment_cancelled", "Cita cancelada"
        APPOINTMENT_COMPLETED = "appointment_completed", "Cita atendida"
        APPOINTMENT_NO_SHOW = "appointment_no_show", "Ausencia registrada"
        SERVICE_CREATED = "service_created", "Servicio creado"
        SERVICE_UPDATED = "service_updated", "Servicio actualizado"
        SERVICE_PAUSED = "service_paused", "Servicio pausado"
        SERVICE_REACTIVATED = "service_reactivated", "Servicio reactivado"
        AVAILABILITY_CREATED = "availability_created", "Horario creado"
        AVAILABILITY_UPDATED = "availability_updated", "Horario actualizado"
        AVAILABILITY_PAUSED = "availability_paused", "Horario pausado"
        AVAILABILITY_REACTIVATED = "availability_reactivated", "Horario reactivado"
        CLOSURE_CREATED = "closure_created", "Cierre creado"
        CLOSURE_UPDATED = "closure_updated", "Cierre actualizado"
        CLOSURE_PAUSED = "closure_paused", "Cierre pausado"
        CLOSURE_REACTIVATED = "closure_reactivated", "Cierre reactivado"
        WORK_LINE_CREATED = "work_line_created", "Línea creada"
        WORK_LINE_UPDATED = "work_line_updated", "Línea actualizada"
        WORK_LINE_PAUSED = "work_line_paused", "Línea pausada"
        WORK_LINE_REACTIVATED = "work_line_reactivated", "Línea reactivada"
        BUSINESS_CREATED = "business_created", "Negocio creado"
        BUSINESS_UPDATED = "business_updated", "Negocio actualizado"
        BUSINESS_PAUSED = "business_paused", "Negocio pausado"
        BUSINESS_REACTIVATED = "business_reactivated", "Negocio reactivado"
        PUBLIC_BOOKING_ENABLED = "public_booking_enabled", "Reserva pública activada"
        PUBLIC_BOOKING_DISABLED = "public_booking_disabled", "Reserva pública pausada"
        VISUAL_SETTINGS_UPDATED = "visual_settings_updated", "Apariencia actualizada"
        MEMBERSHIP_CREATED = "membership_created", "Acceso profesional creado"
        MEMBERSHIP_PAUSED = "membership_paused", "Acceso profesional pausado"
        MEMBERSHIP_REACTIVATED = "membership_reactivated", "Acceso profesional reactivado"

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="activity_events",
        verbose_name="negocio",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="business_activity_events",
        verbose_name="usuario actor",
    )
    actor_type = models.CharField("tipo de actor", max_length=20, choices=ActorType.choices)
    actor_label = models.CharField("actor", max_length=160)
    category = models.CharField("categoría", max_length=24, choices=Category.choices)
    event_type = models.CharField("tipo de evento", max_length=40, choices=EventType.choices)
    origin = models.CharField("origen", max_length=24, choices=Origin.choices)
    summary = models.CharField("resumen", max_length=255)
    entity_type = models.CharField("tipo de entidad", max_length=40, blank=True)
    entity_id = models.PositiveBigIntegerField("identificador de entidad", null=True, blank=True)
    changes = models.JSONField("cambios", default=dict, blank=True)
    created_at = models.DateTimeField("fecha y hora", auto_now_add=True)

    class Meta:
        verbose_name = "movimiento del negocio"
        verbose_name_plural = "movimientos del negocio"
        ordering = ["-id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(summary=""),
                name="business_activity_summary_not_empty",
            )
        ]
        indexes = [
            models.Index(fields=["business", "-id"], name="biz_activity_recent_idx"),
            models.Index(
                fields=["business", "category", "-id"],
                name="biz_activity_cat_idx",
            ),
        ]

    def __str__(self):
        return f"{self.business}: {self.summary}"

# Create your models here.

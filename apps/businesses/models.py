from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q


def business_public_image_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"businesses/{instance.slug}/public-{uuid4().hex}{extension}"


def business_public_gallery_image_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"businesses/{instance.business.slug}/gallery/public-{uuid4().hex}{extension}"


def platform_login_image_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    return f"platform/login/login-{uuid4().hex}{extension}"


class Business(models.Model):
    """Business subscribed to AgendaSalon."""

    class ProfessionalTheme(models.TextChoices):
        LIGHT = "light", "Modo claro"
        DARK = "dark", "Modo oscuro"

    class PublicImagePreset(models.TextChoices):
        SALON = "salon", "Salón luminoso"
        BARBERSHOP = "barberia", "Barbería contemporánea"

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
    legal_compliance_enabled = models.BooleanField(
        "controles legales activos",
        default=False,
        help_text="Se activa en negocios creados desde la plataforma y en negocios existentes migrados.",
    )
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
    public_image_preset = models.CharField(
        "imagen pública predeterminada",
        max_length=16,
        choices=PublicImagePreset.choices,
        default=PublicImagePreset.SALON,
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


class BusinessSignupRequest(models.Model):
    """Solicitud pública revisada antes de crear un negocio en AgendaSalon."""

    class BusinessType(models.TextChoices):
        HAIR_SALON = "hair_salon", "Peluquería"
        BARBERSHOP = "barbershop", "Barbería"
        BEAUTY_SALON = "beauty_salon", "Salón de belleza"
        OTHER = "other", "Otro"

    class PreferredChannel(models.TextChoices):
        PHONE = "phone", "Teléfono"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Correo electrónico"

    class Status(models.TextChoices):
        NEW = "new", "Nueva"
        REVIEWING = "reviewing", "En revisión"
        CONTACTED = "contacted", "Contactada"
        CONVERTED = "converted", "Convertida"
        DISMISSED = "dismissed", "Descartada"

    business_name = models.CharField("nombre comercial", max_length=160)
    business_type = models.CharField(
        "tipo de negocio",
        max_length=24,
        choices=BusinessType.choices,
    )
    city = models.CharField("localidad", max_length=120)
    province = models.CharField("provincia", max_length=120, blank=True)
    contact_name = models.CharField("persona de contacto", max_length=150)
    phone = models.CharField("teléfono", max_length=32)
    normalized_phone = models.CharField("teléfono normalizado", max_length=32, db_index=True)
    email = models.EmailField("correo electrónico", blank=True)
    preferred_channel = models.CharField(
        "canal preferido",
        max_length=16,
        choices=PreferredChannel.choices,
    )
    need_text = models.CharField("necesidad principal", max_length=300, blank=True)
    privacy_document = models.ForeignKey(
        "legal.LegalDocument",
        on_delete=models.PROTECT,
        related_name="business_signup_requests",
        verbose_name="información de privacidad mostrada",
    )
    privacy_document_version = models.CharField("versión de privacidad", max_length=24)
    privacy_document_hash = models.CharField("huella de privacidad", max_length=64)
    privacy_legal_context_snapshot = models.JSONField(
        "contexto legal de privacidad mostrado",
        default=dict,
    )
    privacy_acknowledged_at = models.DateTimeField("información de privacidad leída")
    status = models.CharField(
        "estado",
        max_length=16,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    admin_note = models.TextField("nota interna", max_length=1000, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="handled_business_signup_requests",
        verbose_name="gestionada por",
        null=True,
        blank=True,
    )
    converted_business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="signup_requests",
        verbose_name="negocio creado",
        null=True,
        blank=True,
    )
    converted_at = models.DateTimeField("convertida el", null=True, blank=True)
    created_at = models.DateTimeField("recibida el", auto_now_add=True)
    updated_at = models.DateTimeField("actualizada el", auto_now=True)

    class Meta:
        verbose_name = "solicitud de alta de negocio"
        verbose_name_plural = "solicitudes de alta de negocio"
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="signup_status_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        status="converted",
                        converted_business__isnull=False,
                        converted_at__isnull=False,
                    )
                    | Q(
                        ~Q(status="converted"),
                        converted_business__isnull=True,
                        converted_at__isnull=True,
                    )
                ),
                name="signup_conversion_fields_coherent",
            )
        ]

    @classmethod
    def open_statuses(cls):
        return (cls.Status.NEW, cls.Status.REVIEWING, cls.Status.CONTACTED)

    def clean(self):
        super().clean()
        is_converted = self.status == self.Status.CONVERTED
        has_conversion = bool(self.converted_business_id and self.converted_at)
        if is_converted != has_conversion:
            raise ValidationError(
                "Una solicitud convertida debe conservar el negocio y la fecha de conversión."
            )
        if self.privacy_document_id:
            if self.privacy_document.kind != "platform_privacy":
                raise ValidationError(
                    {"privacy_document": "Debe usarse la privacidad de la plataforma."}
                )
            if self.privacy_document_version != self.privacy_document.version:
                raise ValidationError({"privacy_document_version": "La versión no coincide."})
            if self.privacy_document_hash != self.privacy_document.content_hash:
                raise ValidationError({"privacy_document_hash": "La huella no coincide."})
        if not isinstance(self.privacy_legal_context_snapshot, dict):
            raise ValidationError(
                {
                    "privacy_legal_context_snapshot": (
                        "El contexto legal mostrado debe conservarse como una estructura."
                    )
                }
            )

    def __str__(self):
        return f"{self.business_name} · {self.contact_name}"


class BusinessPublicImage(models.Model):
    """Imagen reutilizable subida por un negocio para sus pantallas públicas."""

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="public_images",
        verbose_name="negocio",
    )
    image = models.ImageField(
        "imagen",
        upload_to=business_public_gallery_image_upload_to,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )
    label = models.CharField("nombre visible", max_length=120)
    is_selected = models.BooleanField("seleccionada", default=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="uploaded_business_public_images",
        verbose_name="subida por",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)

    class Meta:
        verbose_name = "imagen pública de negocio"
        verbose_name_plural = "imágenes públicas de negocio"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["business"],
                condition=models.Q(is_selected=True),
                name="unique_selected_public_image_per_business",
            )
        ]
        indexes = [
            models.Index(
                fields=["business", "created_at"],
                name="pubimg_business_created_idx",
            )
        ]

    def __str__(self):
        return f"{self.label} ({self.business})"


class PlatformSettings(models.Model):
    """Configuración visual única de la administración de AgendaSalon."""

    SINGLETON_PK = 1

    class AdminTheme(models.TextChoices):
        LIGHT = "light", "Modo claro"
        DARK = "dark", "Modo oscuro"

    class LoginImagePreset(models.TextChoices):
        AGENDASALON = "agendasalon", "AgendaSalon"
        SALON = "salon", "Salón luminoso"
        BARBERSHOP = "barberia", "Barbería contemporánea"

    admin_theme = models.CharField(
        "tema de la administración",
        max_length=12,
        choices=AdminTheme.choices,
        default=AdminTheme.LIGHT,
    )
    login_image_preset = models.CharField(
        "imagen predeterminada del acceso interno",
        max_length=16,
        choices=LoginImagePreset.choices,
        default=LoginImagePreset.AGENDASALON,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="updated_platform_settings",
        verbose_name="actualizado por",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "ajustes de plataforma"
        verbose_name_plural = "ajustes de plataforma"

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def __str__(self):
        return "Ajustes de AgendaSalon"


class PlatformLoginImage(models.Model):
    """Imagen reutilizable para el acceso interno de la plataforma."""

    platform_settings = models.ForeignKey(
        PlatformSettings,
        on_delete=models.CASCADE,
        related_name="login_images",
        verbose_name="ajustes de plataforma",
    )
    image = models.ImageField(
        "imagen",
        upload_to=platform_login_image_upload_to,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )
    label = models.CharField("nombre visible", max_length=120)
    is_selected = models.BooleanField("seleccionada", default=False)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="uploaded_platform_login_images",
        verbose_name="subida por",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)

    class Meta:
        verbose_name = "imagen de acceso de plataforma"
        verbose_name_plural = "imágenes de acceso de plataforma"
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform_settings"],
                condition=models.Q(is_selected=True),
                name="unique_selected_platform_login_image",
            )
        ]

    def __str__(self):
        return self.label


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
        CLIENT_INVITATION_CREATED = "client_invitation_created", "Invitación de cliente creada"
        CLIENT_INVITATION_REVOKED = "client_invitation_revoked", "Invitación de cliente revocada"
        CLIENT_ACCESS_ACTIVATED = "client_access_activated", "Cuenta de cliente activada"
        NATIONAL_HOLIDAYS_ENABLED = "national_holidays_enabled", "Festivos nacionales aplicados"
        NATIONAL_HOLIDAYS_DISABLED = "national_holidays_disabled", "Festivos nacionales desactivados"
        LEGAL_DOCUMENTATION_ACCEPTED = (
            "legal_documentation_accepted",
            "Documentación legal aceptada",
        )
        DATA_RIGHTS_REQUEST_UPDATED = (
            "data_rights_request_updated",
            "Solicitud de derechos actualizada",
        )

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

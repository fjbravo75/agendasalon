import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.phone import normalize_phone
from apps.core.text import normalize_search_text


class BusinessClient(models.Model):
    """Customer file scoped to a business."""

    class Source(models.TextChoices):
        PROFESSIONAL = "professional", "Profesional"
        IMPORTED_DEMO = "imported_demo", "Demo importada"
        OTHER = "other", "Otro"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="clients",
        verbose_name="negocio",
    )
    full_name = models.CharField("nombre completo", max_length=160)
    full_name_normalized = models.CharField(
        "nombre normalizado",
        max_length=180,
        editable=False,
    )
    phone = models.CharField("teléfono", max_length=32)
    phone_normalized = models.CharField(
        "teléfono normalizado",
        max_length=32,
        editable=False,
    )
    email = models.EmailField("email", blank=True)
    source = models.CharField(
        "origen",
        max_length=40,
        choices=Source.choices,
        default=Source.PROFESSIONAL,
    )
    is_active = models.BooleanField("activo", default=True)
    internal_notes = models.TextField("notas internas", blank=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)
    last_activity_at = models.DateTimeField("última actividad", null=True, blank=True)

    class Meta:
        verbose_name = "ficha de cliente"
        verbose_name_plural = "fichas de cliente"
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "phone_normalized", "full_name_normalized"],
                condition=models.Q(is_active=True),
                name="unique_active_business_client_identity",
            )
        ]
        indexes = [
            models.Index(fields=["business", "phone_normalized"], name="client_business_phone_idx"),
            models.Index(fields=["business", "full_name_normalized"], name="client_business_name_idx"),
            models.Index(fields=["business", "is_active"], name="client_business_active_idx"),
        ]

    def clean(self):
        super().clean()
        if not self.full_name.strip():
            raise ValidationError({"full_name": "El nombre completo es obligatorio."})
        if not self.phone.strip():
            raise ValidationError({"phone": "El teléfono es obligatorio."})
        self.full_name_normalized = normalize_search_text(self.full_name)
        self.phone_normalized = normalize_phone(self.phone)

    def save(self, *args, **kwargs):
        self.full_name_normalized = normalize_search_text(self.full_name)
        self.phone_normalized = normalize_phone(self.phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.business})"


class BusinessClientAuthorizedContact(models.Model):
    """Authorized contact attached to a customer file, without digital access."""

    class Relationship(models.TextChoices):
        MOTHER = "madre", "Madre"
        FATHER = "padre", "Padre"
        DAUGHTER = "hija", "Hija"
        FAMILY = "familiar", "Familiar"
        CAREGIVER = "cuidador", "Cuidador"
        PARTNER = "pareja", "Pareja"
        OTHER = "otro", "Otro"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="client_authorized_contacts",
        verbose_name="negocio",
    )
    business_client = models.ForeignKey(
        BusinessClient,
        on_delete=models.CASCADE,
        related_name="authorized_contacts",
        verbose_name="ficha de cliente",
    )
    full_name = models.CharField("nombre completo", max_length=160)
    phone = models.CharField("teléfono", max_length=32)
    phone_normalized = models.CharField(
        "teléfono normalizado",
        max_length=32,
        editable=False,
    )
    relationship_label = models.CharField(
        "relación",
        max_length=40,
        choices=Relationship.choices,
        default=Relationship.OTHER,
    )
    is_primary_contact = models.BooleanField("contacto principal", default=False)
    notes = models.TextField("notas", blank=True)
    is_active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "contacto autorizado"
        verbose_name_plural = "contactos autorizados"
        ordering = ["business_client__full_name", "-is_primary_contact", "full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business_client"],
                condition=models.Q(is_primary_contact=True, is_active=True),
                name="unique_primary_active_contact_per_client",
            )
        ]
        indexes = [
            models.Index(fields=["business", "phone_normalized"], name="contact_business_phone_idx"),
            models.Index(fields=["business_client", "is_active"], name="contact_client_active_idx"),
        ]

    def clean(self):
        super().clean()
        if self.business_client_id and self.business_id:
            if self.business_client.business_id != self.business_id:
                raise ValidationError(
                    {"business": "El contacto debe pertenecer al mismo negocio que la ficha."}
                )
        if not self.full_name.strip():
            raise ValidationError({"full_name": "El nombre completo es obligatorio."})
        if not self.phone.strip():
            raise ValidationError({"phone": "El teléfono es obligatorio."})
        self.phone_normalized = normalize_phone(self.phone)

    def save(self, *args, **kwargs):
        if self.business_client_id and not self.business_id:
            self.business = self.business_client.business
        self.phone_normalized = normalize_phone(self.phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} para {self.business_client}"


class BusinessClientAccess(models.Model):
    """Digital access for an end customer of one business."""

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="client_accesses",
        verbose_name="negocio",
    )
    business_client = models.OneToOneField(
        BusinessClient,
        on_delete=models.CASCADE,
        related_name="access",
        verbose_name="ficha de cliente",
    )
    phone = models.CharField("teléfono", max_length=32)
    phone_normalized = models.CharField(
        "teléfono normalizado",
        max_length=32,
        editable=False,
    )
    password_hash = models.CharField("hash de contraseña", max_length=128)
    is_active = models.BooleanField("activo", default=True)
    last_login_at = models.DateTimeField("último acceso", null=True, blank=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "acceso de cliente"
        verbose_name_plural = "accesos de cliente"
        ordering = ["business__commercial_name", "business_client__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "phone_normalized"],
                name="unique_business_client_access_phone",
            )
        ]
        indexes = [
            models.Index(fields=["business", "phone_normalized"], name="client_access_phone_idx"),
            models.Index(fields=["business", "is_active"], name="client_access_active_idx"),
        ]

    def set_password(self, raw_password):
        self.password_hash = make_password(raw_password)

    def check_password(self, raw_password):
        def upgrade_password(password):
            self.set_password(password)
            self.save(update_fields=["password_hash", "updated_at"])

        return check_password(raw_password, self.password_hash, setter=upgrade_password)

    def clean(self):
        super().clean()
        if self.business_client_id and self.business_id:
            if self.business_client.business_id != self.business_id:
                raise ValidationError(
                    {"business_client": "La ficha debe pertenecer al mismo negocio que el acceso."}
                )
        if not self.phone.strip():
            raise ValidationError({"phone": "El teléfono es obligatorio."})
        self.phone_normalized = normalize_phone(self.phone)

    def save(self, *args, **kwargs):
        if self.business_client_id and not self.business_id:
            self.business = self.business_client.business
        self.phone_normalized = normalize_phone(self.phone)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Acceso cliente {self.business_client.full_name} ({self.business})"


class BusinessClientAccessInvitation(models.Model):
    """Invitación de un solo uso para activar la cuenta de una ficha existente."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="client_access_invitations",
        verbose_name="negocio",
    )
    business_client = models.ForeignKey(
        BusinessClient,
        on_delete=models.CASCADE,
        related_name="access_invitations",
        verbose_name="ficha de cliente",
    )
    token_digest = models.CharField("resumen del token", max_length=64, unique=True)
    expires_at = models.DateTimeField("caduca el")
    used_at = models.DateTimeField("usada el", null=True, blank=True)
    revoked_at = models.DateTimeField("revocada el", null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="created_client_access_invitations",
        verbose_name="creada por",
    )
    created_at = models.DateTimeField("fecha de creación", auto_now_add=True)

    class Meta:
        verbose_name = "invitación de acceso de cliente"
        verbose_name_plural = "invitaciones de acceso de cliente"
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["business_client", "expires_at"],
                name="client_invite_active_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.business_client_id and self.business_id:
            if self.business_client.business_id != self.business_id:
                raise ValidationError(
                    {"business_client": "La ficha debe pertenecer al mismo negocio que la invitación."}
                )

    def is_available(self, now=None):
        from django.utils import timezone

        now = now or timezone.now()
        return (
            self.used_at is None
            and self.revoked_at is None
            and self.expires_at > now
            and self.business.is_active
            and self.business_client.is_active
        )

    def __str__(self):
        return f"Invitación para {self.business_client.full_name} ({self.business})"

# Create your models here.

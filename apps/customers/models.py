import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

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
    phone = models.CharField("teléfono", max_length=32, blank=True)
    phone_normalized = models.CharField(
        "teléfono normalizado",
        max_length=32,
        blank=True,
        default="",
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
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="merged_records",
        editable=False,
        verbose_name="ficha resultante",
    )
    merged_at = models.DateTimeField(
        "unificada el",
        null=True,
        blank=True,
        editable=False,
    )
    merged_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merged_client_records",
        editable=False,
        verbose_name="unificada por",
    )
    merge_review_dismissed_fingerprint = models.CharField(
        "coincidencia descartada",
        max_length=64,
        blank=True,
        editable=False,
    )
    merge_review_dismissed_at = models.DateTimeField(
        "coincidencia descartada el",
        null=True,
        blank=True,
        editable=False,
    )
    merge_review_dismissed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dismissed_client_merge_reviews",
        editable=False,
        verbose_name="coincidencia descartada por",
    )

    class Meta:
        verbose_name = "ficha de cliente"
        verbose_name_plural = "fichas de cliente"
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "phone_normalized", "full_name_normalized"],
                condition=(
                    models.Q(is_active=True, source="professional")
                    & ~models.Q(phone_normalized="")
                ),
                name="unique_active_professional_client_identity",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        merged_into__isnull=True,
                        merged_at__isnull=True,
                    )
                    | models.Q(
                        merged_into__isnull=False,
                        merged_at__isnull=False,
                        is_active=False,
                    )
                ),
                name="client_merge_state_consistent",
            ),
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
        self.full_name_normalized = normalize_search_text(self.full_name)
        self.phone_normalized = normalize_phone(self.phone) if self.phone.strip() else ""
        if self.merged_into_id:
            if self.pk and self.merged_into_id == self.pk:
                raise ValidationError({"merged_into": "Una ficha no puede unificarse consigo misma."})
            if self.merged_into.business_id != self.business_id:
                raise ValidationError(
                    {"merged_into": "La ficha resultante debe pertenecer al mismo negocio."}
                )
            if self.is_active:
                raise ValidationError(
                    {"is_active": "Una ficha ya unificada no puede permanecer activa."}
                )

    def save(self, *args, **kwargs):
        self.full_name_normalized = normalize_search_text(self.full_name)
        self.phone_normalized = normalize_phone(self.phone) if self.phone.strip() else ""
        super().save(*args, **kwargs)
        self.authorizations_as_contact.update(
            full_name=self.full_name,
            phone=self.phone,
            phone_normalized=self.phone_normalized,
        )

    def __str__(self):
        return f"{self.full_name} ({self.business})"


class BusinessClientAuthorizedContact(models.Model):
    """Person authorized to request appointments for another customer file."""

    class Relationship(models.TextChoices):
        MOTHER = "madre", "Madre"
        FATHER = "padre", "Padre"
        SON = "hijo", "Hijo"
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
    linked_business_client = models.ForeignKey(
        BusinessClient,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="authorizations_as_contact",
        verbose_name="ficha de la persona autorizada",
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
            ),
            models.UniqueConstraint(
                fields=["business_client", "linked_business_client"],
                condition=models.Q(linked_business_client__isnull=False),
                name="unique_linked_authorized_client_per_file",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "phone_normalized"], name="contact_business_phone_idx"),
            models.Index(fields=["business_client", "is_active"], name="contact_client_active_idx"),
            models.Index(
                fields=["linked_business_client", "is_active"],
                name="contact_linked_client_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.business_client_id and self.business_id:
            if self.business_client.business_id != self.business_id:
                raise ValidationError(
                    {"business": "El contacto debe pertenecer al mismo negocio que la ficha."}
                )
        if self.linked_business_client_id:
            if self.linked_business_client_id == self.business_client_id:
                raise ValidationError(
                    {"linked_business_client": "Una ficha no puede autorizarse a sí misma."}
                )
            if self.linked_business_client.business_id != self.business_id:
                raise ValidationError(
                    {"linked_business_client": "La persona autorizada debe pertenecer al mismo negocio."}
                )
            if not self.linked_business_client.is_active:
                raise ValidationError(
                    {"linked_business_client": "La ficha de la persona autorizada está pausada."}
                )
            self.full_name = self.linked_business_client.full_name
            self.phone = self.linked_business_client.phone
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
    email = models.EmailField("correo electrónico", blank=True)
    email_normalized = models.EmailField(
        "correo normalizado",
        null=True,
        blank=True,
        editable=False,
    )
    email_verified_at = models.DateTimeField(
        "correo verificado el",
        null=True,
        blank=True,
    )
    password_hash = models.CharField("hash de contraseña", max_length=128)
    is_active = models.BooleanField("activo", default=True)
    is_pending_public_registration = models.BooleanField(
        "alta pública pendiente de verificar",
        default=False,
    )
    public_registration_expires_at = models.DateTimeField(
        "caducidad del alta pública",
        null=True,
        blank=True,
        db_index=True,
    )
    last_login_at = models.DateTimeField("último acceso", null=True, blank=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "acceso de cliente"
        verbose_name_plural = "accesos de cliente"
        ordering = ["business__commercial_name", "business_client__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "email_normalized"],
                condition=models.Q(email_normalized__isnull=False),
                name="unique_business_client_access_email",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "phone_normalized"], name="client_access_phone_idx"),
            models.Index(fields=["business", "is_active"], name="client_access_active_idx"),
        ]

    def set_password(self, raw_password):
        self.password_hash = make_password(raw_password)

    def check_password(self, raw_password):
        encoded_password = self.password_hash
        upgraded_password = None

        def upgrade_password(password):
            nonlocal upgraded_password
            upgraded_password = make_password(password)

        password_matches = check_password(
            raw_password,
            encoded_password,
            setter=upgrade_password,
        )
        if not password_matches or self.pk is None:
            return password_matches

        queryset = type(self).objects.filter(
            pk=self.pk,
            password_hash=encoded_password,
        )
        if upgraded_password is None:
            # Un reset concurrente invalida también una instancia que hubiera
            # verificado la contraseña anterior justo antes del cambio.
            return queryset.exists()

        # El rehash es optimista: solo sustituye exactamente el hash que se
        # verificó. Nunca puede pisar una contraseña cambiada por otro flujo.
        updated = queryset.update(
            password_hash=upgraded_password,
            updated_at=timezone.now(),
        )
        if updated != 1:
            return False
        self.password_hash = upgraded_password
        return True

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
        self.email = (self.email or "").strip()
        self.email_normalized = self.email.lower() or None

    def save(self, *args, **kwargs):
        if self.business_client_id and not self.business_id:
            self.business = self.business_client.business
        self.phone_normalized = normalize_phone(self.phone)
        self.email = (self.email or "").strip()
        self.email_normalized = self.email.lower() or None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Acceso cliente {self.business_client.full_name} ({self.business})"


class BusinessClientAccessGrant(models.Model):
    """Permiso de una cuenta online para reservar para una ficha concreta."""

    class Relationship(models.TextChoices):
        SELF = "titular", "Es su propia ficha"
        MOTHER = "madre", "Madre"
        FATHER = "padre", "Padre"
        SON = "hijo", "Hijo"
        DAUGHTER = "hija", "Hija"
        FAMILY = "familiar", "Familiar"
        CAREGIVER = "cuidador", "Cuidador"
        PARTNER = "pareja", "Pareja"
        OTHER = "otro", "Otra relación"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="client_access_grants",
        verbose_name="negocio",
    )
    access = models.ForeignKey(
        BusinessClientAccess,
        on_delete=models.CASCADE,
        related_name="booking_grants",
        verbose_name="cuenta online",
    )
    business_client = models.ForeignKey(
        BusinessClient,
        on_delete=models.CASCADE,
        related_name="online_booking_grants",
        verbose_name="ficha para la que puede reservar",
    )
    authorized_contact = models.ForeignKey(
        BusinessClientAuthorizedContact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="online_booking_grants",
        verbose_name="persona autorizada",
    )
    relationship_label = models.CharField(
        "relación",
        max_length=40,
        choices=Relationship.choices,
        default=Relationship.OTHER,
    )
    is_active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "permiso de reserva online"
        verbose_name_plural = "permisos de reserva online"
        ordering = ["business_client__full_name", "access__business_client__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["access", "business_client"],
                name="unique_access_booking_grant_per_client",
            )
        ]
        indexes = [
            models.Index(
                fields=["access", "is_active"],
                name="access_grant_active_idx",
            ),
            models.Index(
                fields=["business_client", "is_active"],
                name="client_grant_active_idx",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.access_id and self.business_id and self.access.business_id != self.business_id:
            errors["access"] = "La cuenta debe pertenecer al mismo negocio."
        if (
            self.business_client_id
            and self.business_id
            and self.business_client.business_id != self.business_id
        ):
            errors["business_client"] = "La ficha debe pertenecer al mismo negocio."
        if self.authorized_contact_id:
            if self.authorized_contact.business_client_id != self.business_client_id:
                errors["authorized_contact"] = "La persona autorizada debe pertenecer a esta ficha."
            elif self.authorized_contact.business_id != self.business_id:
                errors["authorized_contact"] = "La persona autorizada debe pertenecer al mismo negocio."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.access_id and not self.business_id:
            self.business = self.access.business
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.access.business_client.full_name} puede reservar para {self.business_client.full_name}"


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

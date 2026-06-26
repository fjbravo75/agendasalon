from django.conf import settings
from django.db import models


class Business(models.Model):
    """Business subscribed to AgendaSalon."""

    commercial_name = models.CharField("nombre comercial", max_length=160)
    slug = models.SlugField("slug", max_length=180, unique=True)
    public_description = models.TextField("descripcion publica", blank=True)
    public_phone = models.CharField("telefono publico", max_length=32, blank=True)
    public_email = models.EmailField("email publico", blank=True)
    address = models.CharField("direccion", max_length=255, blank=True)
    city = models.CharField("localidad", max_length=120, blank=True)
    province = models.CharField("provincia", max_length=120, blank=True)
    is_active = models.BooleanField("activo", default=True)
    last_activity_at = models.DateTimeField("ultima actividad", null=True, blank=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("ultima actualizacion", auto_now=True)

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
    updated_at = models.DateTimeField("ultima actualizacion", auto_now=True)

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

# Create your models here.

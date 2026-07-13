import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class LegalDocument(models.Model):
    """Versión publicada e inmutable de un documento legal de AgendaSalon."""

    class Kind(models.TextChoices):
        LEGAL_NOTICE = "legal_notice", "Aviso legal"
        PLATFORM_PRIVACY = "platform_privacy", "Privacidad de la plataforma"
        TERMS = "terms", "Condiciones del servicio"
        DATA_PROCESSING = "data_processing", "Encargo de tratamiento"
        CUSTOMER_PRIVACY = "customer_privacy", "Privacidad de clientes"
        COOKIES = "cookies", "Política de cookies"

    kind = models.CharField("tipo", max_length=32, choices=Kind.choices)
    slug = models.SlugField("identificador público", max_length=80)
    version = models.CharField("versión", max_length=24)
    title = models.CharField("título", max_length=180)
    lead = models.TextField("introducción")
    sections = models.JSONField("secciones", default=list)
    content_hash = models.CharField("huella SHA-256", max_length=64, editable=False)
    published_at = models.DateTimeField("publicado el", default=timezone.now)
    is_active = models.BooleanField("vigente", default=True)

    class Meta:
        verbose_name = "documento legal"
        verbose_name_plural = "documentos legales"
        ordering = ["kind", "-published_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "version"],
                name="unique_legal_document_kind_version",
            ),
            models.UniqueConstraint(
                fields=["kind"],
                condition=Q(is_active=True),
                name="unique_active_legal_document_kind",
            ),
        ]

    def canonical_payload(self):
        return {
            "kind": self.kind,
            "slug": self.slug,
            "version": self.version,
            "title": self.title,
            "lead": self.lead,
            "sections": self.sections,
        }

    def calculate_hash(self):
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def clean(self):
        super().clean()
        if not isinstance(self.sections, list) or not self.sections:
            raise ValidationError({"sections": "El documento debe contener al menos una sección."})
        for section in self.sections:
            if not isinstance(section, dict) or not section.get("heading"):
                raise ValidationError({"sections": "Cada sección debe tener un encabezado."})

        if self.pk:
            previous = LegalDocument.objects.filter(pk=self.pk).first()
            if previous and previous.content_hash and previous.content_hash != self.calculate_hash():
                raise ValidationError(
                    "Una versión publicada es inmutable. Crea una versión nueva para cambiar su contenido."
                )

    def save(self, *args, **kwargs):
        self.content_hash = self.calculate_hash()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()} · {self.version}"


class BusinessLegalProfile(models.Model):
    """Identidad y criterio de conservación del responsable de cada salón."""

    business = models.OneToOneField(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="legal_profile",
        verbose_name="negocio",
    )
    legal_name = models.CharField("nombre o razón social", max_length=180)
    tax_identifier = models.CharField("identificación fiscal", max_length=40)
    registered_address = models.CharField("domicilio", max_length=255)
    privacy_email = models.EmailField("correo para privacidad")
    rights_contact_name = models.CharField(
        "persona o área de contacto",
        max_length=160,
        blank=True,
    )
    retention_criteria = models.TextField("criterio de conservación")
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "perfil legal de negocio"
        verbose_name_plural = "perfiles legales de negocio"

    @property
    def is_complete(self):
        return all(
            str(value).strip()
            for value in (
                self.legal_name,
                self.tax_identifier,
                self.registered_address,
                self.privacy_email,
                self.retention_criteria,
            )
        )

    def snapshot(self):
        return {
            "legal_name": self.legal_name,
            "tax_identifier": self.tax_identifier,
            "registered_address": self.registered_address,
            "privacy_email": self.privacy_email,
            "rights_contact_name": self.rights_contact_name,
            "retention_criteria": self.retention_criteria,
        }

    def __str__(self):
        return f"Perfil legal de {self.business}"


class LegalAcceptance(models.Model):
    """Evidencia mínima de información, aceptación contractual o autorización."""

    class Action(models.TextChoices):
        ACKNOWLEDGED = "acknowledged", "Información recibida"
        ACCEPTED = "accepted", "Documento aceptado"

    class Context(models.TextChoices):
        PROFESSIONAL_ONBOARDING = "professional_onboarding", "Alta legal del negocio"
        CLIENT_REGISTRATION = "client_registration", "Registro de cliente"
        CLIENT_INVITATION = "client_invitation", "Activación por invitación"
        BOOKING_CONFIRMATION = "booking_confirmation", "Confirmación de cita"

    document = models.ForeignKey(
        LegalDocument,
        on_delete=models.PROTECT,
        related_name="acceptances",
        verbose_name="documento",
    )
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="legal_acceptances",
        verbose_name="negocio",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_acceptances",
        verbose_name="profesional",
    )
    client_access = models.ForeignKey(
        "customers.BusinessClientAccess",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_acceptances",
        verbose_name="cuenta cliente",
    )
    action = models.CharField("acción", max_length=20, choices=Action.choices)
    context = models.CharField("contexto", max_length=32, choices=Context.choices)
    document_hash_snapshot = models.CharField("huella aceptada", max_length=64)
    legal_context_snapshot = models.JSONField("contexto legal mostrado", default=dict)
    authority_declared = models.BooleanField("autoridad declarada", default=False)
    accepted_at = models.DateTimeField("fecha y hora", auto_now_add=True)

    class Meta:
        verbose_name = "aceptación legal"
        verbose_name_plural = "aceptaciones legales"
        ordering = ["-accepted_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(actor_user__isnull=False, client_access__isnull=True)
                    | Q(actor_user__isnull=True, client_access__isnull=False)
                ),
                name="legal_acceptance_exactly_one_actor",
            ),
            models.UniqueConstraint(
                fields=["document", "business", "actor_user", "context"],
                condition=Q(actor_user__isnull=False),
                name="unique_user_legal_acceptance_context",
            ),
            models.UniqueConstraint(
                fields=["document", "business", "client_access", "context"],
                condition=Q(client_access__isnull=False),
                name="unique_client_legal_acceptance_context",
            ),
        ]
        indexes = [
            models.Index(
                fields=["business", "context", "-accepted_at"],
                name="legal_acceptance_business_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.document_id and self.document_hash_snapshot != self.document.content_hash:
            raise ValidationError({"document_hash_snapshot": "La huella no coincide con el documento."})
        if self.client_access_id and self.client_access.business_id != self.business_id:
            raise ValidationError({"client_access": "La cuenta debe pertenecer al mismo negocio."})

    def __str__(self):
        return f"{self.document} · {self.business}"


class DataRightsRequest(models.Model):
    """Solicitud registrada por una cuenta cliente para su gestión por el negocio."""

    class RequestType(models.TextChoices):
        ACCESS = "access", "Acceso"
        RECTIFICATION = "rectification", "Rectificación"
        ERASURE = "erasure", "Supresión"
        RESTRICTION = "restriction", "Limitación"
        PORTABILITY = "portability", "Portabilidad"
        OBJECTION = "objection", "Oposición"

    class Status(models.TextChoices):
        RECEIVED = "received", "Recibida"
        IN_PROGRESS = "in_progress", "En revisión"
        RESOLVED = "resolved", "Resuelta"
        DISMISSED = "dismissed", "No procede"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="data_rights_requests",
        verbose_name="negocio",
    )
    client_access = models.ForeignKey(
        "customers.BusinessClientAccess",
        on_delete=models.PROTECT,
        related_name="data_rights_requests",
        verbose_name="cuenta cliente",
    )
    request_type = models.CharField("derecho", max_length=20, choices=RequestType.choices)
    detail = models.TextField("detalle", blank=True)
    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    resolution_note = models.TextField("nota de resolución", blank=True)
    created_at = models.DateTimeField("recibida el", auto_now_add=True)
    updated_at = models.DateTimeField("actualizada el", auto_now=True)
    resolved_at = models.DateTimeField("resuelta el", null=True, blank=True)

    class Meta:
        verbose_name = "solicitud de derechos"
        verbose_name_plural = "solicitudes de derechos"
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(
                fields=["business", "status", "-created_at"],
                name="rights_request_business_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.client_access_id and self.client_access.business_id != self.business_id:
            raise ValidationError({"client_access": "La cuenta debe pertenecer al mismo negocio."})

    def save(self, *args, **kwargs):
        if self.status in {self.Status.RESOLVED, self.Status.DISMISSED}:
            self.resolved_at = self.resolved_at or timezone.now()
        else:
            self.resolved_at = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_request_type_display()} · {self.client_access.business_client.full_name}"

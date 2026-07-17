import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


IMMUTABLE_LEGAL_EVENT_MESSAGE = (
    "Las constancias históricas no se pueden modificar ni borrar."
)
IMMUTABLE_LEGAL_DOCUMENT_MESSAGE = (
    "Una versión publicada es inmutable. Crea una versión nueva para cambiar su contenido."
)


class AppendOnlyLegalEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise TypeError(IMMUTABLE_LEGAL_EVENT_MESSAGE)

    def delete(self):
        raise TypeError(IMMUTABLE_LEGAL_EVENT_MESSAGE)

    def bulk_update(self, objs, fields, batch_size=None):
        raise TypeError(IMMUTABLE_LEGAL_EVENT_MESSAGE)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if ignore_conflicts or update_conflicts:
            raise TypeError(IMMUTABLE_LEGAL_EVENT_MESSAGE)
        objs = list(objs)
        for obj in objs:
            obj.full_clean(validate_unique=False)
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=False,
            update_conflicts=False,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class AppendOnlyLegalEvent(models.Model):
    objects = AppendOnlyLegalEventQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if (
            not self._state.adding
            or kwargs.get("force_update")
            or kwargs.get("update_fields") is not None
        ):
            raise ValidationError(IMMUTABLE_LEGAL_EVENT_MESSAGE)
        self.full_clean(validate_unique=False)
        kwargs["force_insert"] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError(IMMUTABLE_LEGAL_EVENT_MESSAGE)


class LegalDocumentQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if set(kwargs) - {"is_active"}:
            raise TypeError(IMMUTABLE_LEGAL_DOCUMENT_MESSAGE)
        return super().update(**kwargs)

    def delete(self):
        raise TypeError(IMMUTABLE_LEGAL_DOCUMENT_MESSAGE)

    def bulk_update(self, objs, fields, batch_size=None):
        if set(fields) - {"is_active"}:
            raise TypeError(IMMUTABLE_LEGAL_DOCUMENT_MESSAGE)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        if ignore_conflicts or update_conflicts:
            raise TypeError(IMMUTABLE_LEGAL_DOCUMENT_MESSAGE)
        objs = list(objs)
        for obj in objs:
            obj.content_hash = obj.calculate_hash()
            obj.full_clean(validate_unique=False, validate_constraints=False)
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=False,
            update_conflicts=False,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class LegalDocument(models.Model):
    """Versión publicada e inmutable de un documento legal de AgendaSalon."""

    objects = LegalDocumentQuerySet.as_manager()

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

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and set(update_fields) - {"is_active"}:
            raise ValidationError(IMMUTABLE_LEGAL_DOCUMENT_MESSAGE)
        previous = (
            type(self).objects.filter(pk=self.pk).first()
            if self.pk
            else None
        )
        if previous is not None:
            immutable_fields = (
                "kind",
                "slug",
                "version",
                "title",
                "lead",
                "sections",
                "content_hash",
                "published_at",
            )
            if any(
                getattr(previous, field_name) != getattr(self, field_name)
                for field_name in immutable_fields
            ):
                raise ValidationError(IMMUTABLE_LEGAL_DOCUMENT_MESSAGE)
        else:
            self.content_hash = self.calculate_hash()
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise TypeError(IMMUTABLE_LEGAL_DOCUMENT_MESSAGE)

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
    """Snapshot actual compatible de una aceptación o información legal."""

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


class LegalAcceptanceEvent(AppendOnlyLegalEvent):
    """Evento inmutable que conserva cada acción legal realizada."""

    document = models.ForeignKey(
        LegalDocument,
        on_delete=models.PROTECT,
        related_name="acceptance_events",
        verbose_name="documento",
    )
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="legal_acceptance_events",
        verbose_name="negocio",
    )
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_acceptance_events",
        verbose_name="profesional",
    )
    client_access = models.ForeignKey(
        "customers.BusinessClientAccess",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="legal_acceptance_events",
        verbose_name="cuenta cliente",
    )
    action = models.CharField(
        "acción",
        max_length=20,
        choices=LegalAcceptance.Action.choices,
    )
    context = models.CharField(
        "contexto",
        max_length=32,
        choices=LegalAcceptance.Context.choices,
    )
    document_hash_snapshot = models.CharField("huella aceptada", max_length=64)
    legal_context_snapshot = models.JSONField("contexto legal mostrado", default=dict)
    authority_declared = models.BooleanField("declaró tener autorización", default=False)
    accepted_at = models.DateTimeField("fecha y hora", default=timezone.now)
    recorded_at = models.DateTimeField(
        "registrado el",
        default=timezone.now,
        editable=False,
    )
    action_fingerprint = models.CharField(
        "identificador único de la acción",
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        editable=False,
    )

    class Meta:
        verbose_name = "evento de aceptación legal"
        verbose_name_plural = "eventos de aceptación legal"
        ordering = ["-accepted_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(actor_user__isnull=False, client_access__isnull=True)
                    | Q(actor_user__isnull=True, client_access__isnull=False)
                ),
                name="legal_event_exactly_one_actor",
            ),
        ]
        indexes = [
            models.Index(
                fields=["business", "context", "-accepted_at"],
                name="legal_event_business_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.document_id and self.document_hash_snapshot != self.document.content_hash:
            raise ValidationError(
                {"document_hash_snapshot": "La huella no coincide con el documento."}
            )
        if self.client_access_id and self.client_access.business_id != self.business_id:
            raise ValidationError(
                {"client_access": "La cuenta debe pertenecer al mismo negocio."}
            )

    def __str__(self):
        return f"{self.document} · {self.business} · {self.accepted_at:%Y-%m-%d %H:%M}"


class CustomerPrivacyEvidence(models.Model):
    """Constancia versionada de la información facilitada a una persona cliente."""

    class EventType(models.TextChoices):
        ACKNOWLEDGED = "acknowledged", "Información leída por la persona cliente"
        INFORMATION_PROVIDED = "information_provided", "Información facilitada por el negocio"

    class Channel(models.TextChoices):
        ONLINE_REGISTRATION = "online_registration", "Registro online"
        CLIENT_INVITATION = "client_invitation", "Invitación online"
        BOOKING = "booking", "Confirmación de reserva"
        PHONE = "phone", "Teléfono"
        WHATSAPP = "whatsapp", "WhatsApp"
        IN_PERSON = "in_person", "En el establecimiento"
        EMAIL = "email", "Correo electrónico"
        OTHER = "other", "Otro canal"

    class InformedParty(models.TextChoices):
        CLIENT = "client", "Cliente"
        AUTHORIZED_PERSON = "authorized_person", "Persona autorizada"

    document = models.ForeignKey(
        LegalDocument,
        on_delete=models.PROTECT,
        related_name="customer_privacy_evidence",
        verbose_name="documento",
    )
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="customer_privacy_evidence",
        verbose_name="negocio",
    )
    business_client = models.ForeignKey(
        "customers.BusinessClient",
        on_delete=models.PROTECT,
        related_name="privacy_evidence",
        verbose_name="cliente",
    )
    client_access = models.ForeignKey(
        "customers.BusinessClientAccess",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="privacy_evidence",
        verbose_name="cuenta cliente",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recorded_customer_privacy_evidence",
        verbose_name="registrado por",
    )
    event_type = models.CharField("tipo de constancia", max_length=24, choices=EventType.choices)
    channel = models.CharField("canal", max_length=24, choices=Channel.choices)
    informed_party_type = models.CharField(
        "persona informada",
        max_length=24,
        choices=InformedParty.choices,
        default=InformedParty.CLIENT,
    )
    informed_party_name_snapshot = models.CharField(
        "nombre de la persona informada",
        max_length=160,
    )
    document_hash_snapshot = models.CharField("huella mostrada", max_length=64)
    legal_context_snapshot = models.JSONField("contexto legal mostrado", default=dict)
    occurred_at = models.DateTimeField("fecha y hora del hecho", default=timezone.now)
    created_at = models.DateTimeField("registrado el", auto_now_add=True)

    class Meta:
        verbose_name = "constancia de privacidad de cliente"
        verbose_name_plural = "constancias de privacidad de clientes"
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(
                fields=["business_client", "document", "-occurred_at"],
                name="customer_privacy_client_idx",
            ),
            models.Index(
                fields=["business", "document", "-occurred_at"],
                name="customer_privacy_business_idx",
            ),
        ]

    def clean(self):
        super().clean()
        if self.document_id and self.document.kind != LegalDocument.Kind.CUSTOMER_PRIVACY:
            raise ValidationError({"document": "Debe ser una política de privacidad de clientes."})
        if self.document_id and self.document_hash_snapshot != self.document.content_hash:
            raise ValidationError({"document_hash_snapshot": "La huella no coincide con el documento."})
        if self.business_client_id and self.business_client.business_id != self.business_id:
            raise ValidationError({"business_client": "La ficha debe pertenecer al mismo negocio."})
        if self.client_access_id:
            if self.client_access.business_id != self.business_id:
                raise ValidationError({"client_access": "La cuenta debe pertenecer al mismo negocio."})
            if self.client_access.business_client_id != self.business_client_id:
                raise ValidationError({"client_access": "La cuenta debe corresponder a la misma ficha."})
        if self.event_type == self.EventType.ACKNOWLEDGED and not self.client_access_id:
            raise ValidationError({"client_access": "La lectura online debe estar vinculada a una cuenta."})
        if self.event_type == self.EventType.INFORMATION_PROVIDED and not self.recorded_by_id:
            raise ValidationError({"recorded_by": "La entrega manual debe identificar al profesional."})
        if not self.informed_party_name_snapshot.strip():
            raise ValidationError(
                {"informed_party_name_snapshot": "Debe identificarse a la persona informada."}
            )

    def __str__(self):
        return f"{self.business_client} · {self.get_channel_display()} · {self.document.version}"


class CustomerPrivacyEvidenceEvent(AppendOnlyLegalEvent):
    """Evento inmutable de información de privacidad facilitada a un cliente."""

    document = models.ForeignKey(
        LegalDocument,
        on_delete=models.PROTECT,
        related_name="customer_privacy_evidence_events",
        verbose_name="documento",
    )
    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.PROTECT,
        related_name="customer_privacy_evidence_events",
        verbose_name="negocio",
    )
    business_client = models.ForeignKey(
        "customers.BusinessClient",
        on_delete=models.PROTECT,
        related_name="privacy_evidence_events",
        verbose_name="cliente",
    )
    client_access = models.ForeignKey(
        "customers.BusinessClientAccess",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="privacy_evidence_events",
        verbose_name="cuenta cliente",
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="recorded_customer_privacy_evidence_events",
        verbose_name="registrado por",
    )
    event_type = models.CharField(
        "tipo de constancia",
        max_length=24,
        choices=CustomerPrivacyEvidence.EventType.choices,
    )
    channel = models.CharField(
        "canal",
        max_length=24,
        choices=CustomerPrivacyEvidence.Channel.choices,
    )
    informed_party_type = models.CharField(
        "persona informada",
        max_length=24,
        choices=CustomerPrivacyEvidence.InformedParty.choices,
        default=CustomerPrivacyEvidence.InformedParty.CLIENT,
    )
    informed_party_name_snapshot = models.CharField(
        "nombre de la persona informada",
        max_length=160,
    )
    document_hash_snapshot = models.CharField("huella mostrada", max_length=64)
    legal_context_snapshot = models.JSONField("contexto legal mostrado", default=dict)
    occurred_at = models.DateTimeField("fecha y hora del hecho", default=timezone.now)
    recorded_at = models.DateTimeField(
        "registrado el",
        default=timezone.now,
        editable=False,
    )
    action_fingerprint = models.CharField(
        "identificador único de la acción",
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        editable=False,
    )

    class Meta:
        verbose_name = "evento de privacidad de cliente"
        verbose_name_plural = "eventos de privacidad de clientes"
        ordering = ["-occurred_at", "-pk"]
        indexes = [
            models.Index(
                fields=["business_client", "document", "-occurred_at"],
                name="privacy_event_client_idx",
            ),
            models.Index(
                fields=["business", "document", "-occurred_at"],
                name="privacy_event_business_idx",
            ),
        ]

    def clean(self):
        super().clean()
        projection = CustomerPrivacyEvidence(
            document=self.document,
            business=self.business,
            business_client=self.business_client,
            client_access=self.client_access,
            recorded_by=self.recorded_by,
            event_type=self.event_type,
            channel=self.channel,
            informed_party_type=self.informed_party_type,
            informed_party_name_snapshot=self.informed_party_name_snapshot,
            document_hash_snapshot=self.document_hash_snapshot,
            legal_context_snapshot=self.legal_context_snapshot,
            occurred_at=self.occurred_at,
        )
        projection.full_clean(exclude={"id"})

    def __str__(self):
        return f"{self.business_client} · {self.get_channel_display()} · {self.document.version}"


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

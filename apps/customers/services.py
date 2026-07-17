import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db import models
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.phone import normalize_phone
from apps.core.text import normalize_search_text
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessGrant,
    BusinessClientAccessInvitation,
    BusinessClientAuthorizedContact,
)


CLIENT_ACCESS_SESSION_KEY = "business_client_access_id"
CLIENT_ACCESS_LAST_SEEN_SESSION_KEY = "business_client_access_last_seen"
CLIENT_ACCESS_PASSWORD_SESSION_KEY = "business_client_access_password_fingerprint"
CLIENT_INVITATION_CLAIM_SESSION_KEY = "business_client_invitation_claim"
CLIENT_ACCESS_IDLE_SECONDS = 60 * 60
CLIENT_INVITATION_LIFETIME_HOURS = 24
PUBLIC_REGISTRATION_RETENTION_SECONDS = 48 * 60 * 60
PUBLIC_REGISTRATION_UNAVAILABLE_MESSAGE = (
    "No podemos crear una cuenta con esos datos. Contacta con el negocio para activar tu acceso."
)


def public_registration_expiry(*, now=None):
    return (now or timezone.now()) + timedelta(
        seconds=PUBLIC_REGISTRATION_RETENTION_SECONDS
    )


@dataclass(frozen=True)
class PublicRegistrationPurgeResult:
    candidates: int = 0
    eligible: int = 0
    purged: int = 0
    skipped: int = 0

    def merged(self, other):
        return PublicRegistrationPurgeResult(
            candidates=self.candidates + other.candidates,
            eligible=self.eligible + other.eligible,
            purged=self.purged + other.purged,
            skipped=self.skipped + other.skipped,
        )


def _pending_registration_has_unsafe_usage(client, access):
    """Impide borrar una identidad pendiente que ya tenga actividad ajena al alta."""

    return (
        client.last_activity_at is not None
        or access.last_login_at is not None
        or client.appointments.exists()
        or access.requested_appointments.exists()
        or client.notifications.exists()
        or client.access_invitations.exists()
        or client.authorized_contacts.exists()
        or client.authorizations_as_contact.exists()
        or client.privacy_evidence.exists()
        or client.privacy_evidence_events.exists()
        or access.legal_acceptances.exists()
        or access.legal_acceptance_events.exists()
        or access.privacy_evidence.exists()
        or access.privacy_evidence_events.exists()
        or access.data_rights_requests.exists()
        or client.online_booking_grants.exclude(access=access).exists()
        or access.booking_grants.exclude(business_client=client).exists()
    )


def _pending_registration_outbox_is_safe_to_purge(
    access,
    *,
    now,
    dry_run=False,
):
    """Devuelve (purgable, acción sobre lease caducado).

    La segunda posición también es verdadera en ``dry_run`` cuando la ejecución
    real cancelaría al menos una reserva ``PROCESSING`` caducada. Así el límite
    del lote representa las mismas acciones útiles en simulación y en purga.
    """

    from apps.notifications.models import OutboundEmail

    emails = tuple(
        OutboundEmail.objects.select_for_update()
        .filter(client_access=access)
        .only("pk", "kind", "status", "lease_token", "lease_expires_at")
        .order_by("pk")
    )
    if any(
        email.kind != OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION
        for email in emails
    ):
        return False, False

    processing = tuple(
        email for email in emails if email.status == OutboundEmail.Status.PROCESSING
    )
    if not processing:
        return True, False

    stale_ids = tuple(
        email.pk
        for email in processing
        if email.lease_expires_at is None or email.lease_expires_at <= now
    )
    if stale_ids and not dry_run:
        OutboundEmail.objects.filter(
            pk__in=stale_ids,
            status=OutboundEmail.Status.PROCESSING,
        ).update(
            status=OutboundEmail.Status.CANCELLED,
            lease_token=None,
            lease_expires_at=None,
            last_error=(
                "La reserva de envío había caducado al limpiar el alta pendiente."
            ),
            updated_at=now,
        )
    # Incluso una reserva caducada se conserva durante esta ejecución. Así un
    # worker rezagado puede comprobar que ya no posee el lease antes de que la
    # siguiente pasada elimine el grafo completo.
    return False, bool(stale_ids)


def _purge_expired_public_registrations_for_locked_business(
    business,
    *,
    now,
    email_normalized=None,
    batch_size=None,
    dry_run=False,
):
    """Procesa hasta ``batch_size`` acciones útiles, no primeras candidatas.

    Una acción útil es una eliminación efectiva —o purgable en simulación— o la
    cancelación, real o simulada, de un lease ``PROCESSING`` caducado. Las altas
    protegidas se examinan y cuentan como omitidas, pero nunca consumen el lote.
    El recorrido por clave primaria evita que un prefijo protegido impida llegar
    indefinidamente a registros posteriores que sí pueden limpiarse.
    """

    legacy_cutoff = now - timedelta(seconds=PUBLIC_REGISTRATION_RETENTION_SECONDS)
    expired_filter = models.Q(public_registration_expires_at__lte=now) | models.Q(
        public_registration_expires_at__isnull=True,
        created_at__lte=legacy_cutoff,
    )
    candidates = BusinessClient.objects.filter(
        business=business,
        source=BusinessClient.Source.OTHER,
        is_active=False,
        access__is_pending_public_registration=True,
        access__email_verified_at__isnull=True,
    ).filter(models.Q(access__public_registration_expires_at__lte=now) | models.Q(
        access__public_registration_expires_at__isnull=True,
        access__created_at__lte=legacy_cutoff,
    ))
    if email_normalized:
        candidates = candidates.filter(access__email_normalized=email_normalized)

    candidates_examined = 0
    eligible = 0
    purged = 0
    skipped = 0
    useful_actions = 0
    last_client_id = 0
    limit_reached = False

    while not limit_reached:
        client_ids = tuple(
            candidates.filter(pk__gt=last_client_id)
            .order_by("pk")
            .values_list("pk", flat=True)[:200]
        )
        if not client_ids:
            break

        for client_id in client_ids:
            last_client_id = client_id
            candidates_examined += 1
            client = BusinessClient.objects.select_for_update().get(
                pk=client_id,
                business=business,
            )
            access = (
                BusinessClientAccess.objects.select_for_update()
                .filter(
                    business=business,
                    business_client=client,
                    is_pending_public_registration=True,
                    email_verified_at__isnull=True,
                )
                .filter(expired_filter)
                .first()
            )
            if (
                access is None
                or client.is_active
                or _pending_registration_has_unsafe_usage(client, access)
            ):
                skipped += 1
                continue

            outbox_is_safe, stale_lease_action = (
                _pending_registration_outbox_is_safe_to_purge(
                    access,
                    now=now,
                    dry_run=dry_run,
                )
            )
            if stale_lease_action:
                useful_actions += 1
            if not outbox_is_safe:
                skipped += 1
            else:
                eligible += 1
                if dry_run:
                    useful_actions += 1
                else:
                    try:
                        with transaction.atomic():
                            client.delete()
                    except ProtectedError:
                        skipped += 1
                    else:
                        purged += 1
                        useful_actions += 1

            if batch_size is not None and useful_actions >= batch_size:
                limit_reached = True
                break

    return PublicRegistrationPurgeResult(
        candidates=candidates_examined,
        eligible=eligible,
        purged=purged,
        skipped=skipped,
    )


def purge_expired_public_registrations(
    *,
    business_id=None,
    now=None,
    batch_size=200,
    dry_run=False,
):
    """Purga altas caducadas con un lote de acciones útiles por negocio.

    Las candidatas omitidas por seguridad no consumen ``batch_size``.
    """

    now = now or timezone.now()
    if batch_size is not None and batch_size <= 0:
        raise ValueError("batch_size debe ser mayor que cero.")
    businesses = Business.objects.order_by("pk").values_list("pk", flat=True)
    if business_id is not None:
        businesses = businesses.filter(pk=business_id)

    result = PublicRegistrationPurgeResult()
    for current_business_id in businesses.iterator():
        with transaction.atomic():
            locked_business = Business.objects.select_for_update().get(
                pk=current_business_id
            )
            result = result.merged(
                _purge_expired_public_registrations_for_locked_business(
                    locked_business,
                    now=now,
                    batch_size=batch_size,
                    dry_run=dry_run,
                )
            )
    return result


def ensure_self_booking_grant(access):
    grant, _ = BusinessClientAccessGrant.objects.get_or_create(
        business=access.business,
        access=access,
        business_client=access.business_client,
        defaults={
            "relationship_label": BusinessClientAccessGrant.Relationship.SELF,
            "is_active": True,
        },
    )
    if not grant.is_active:
        grant.is_active = True
        grant.save(update_fields=["is_active", "updated_at"])
    return grant


def get_bookable_clients(access):
    return (
        BusinessClient.objects.filter(
            business=access.business,
            is_active=True,
        )
        .filter(
            models.Q(pk=access.business_client_id)
            | (
                models.Q(
                    online_booking_grants__access=access,
                    online_booking_grants__is_active=True,
                )
                & (
                    models.Q(online_booking_grants__authorized_contact__isnull=True)
                    | models.Q(online_booking_grants__authorized_contact__is_active=True)
                )
            )
        )
        .distinct()
        .order_by("full_name", "pk")
    )


def get_bookable_client(access, client_id):
    return get_bookable_clients(access).filter(pk=client_id).first()


def authenticate_client_access(
    *, business, identifier: str | None = None, phone: str | None = None, password: str
):
    """Autentica por correo o, como compatibilidad, por un teléfono no ambiguo."""

    identifier = (identifier if identifier is not None else phone or "").strip()
    queryset = BusinessClientAccess.objects.select_related("business_client", "business").filter(
        business=business,
        is_active=True,
        email_verified_at__isnull=False,
        business_client__is_active=True,
    )
    if "@" in identifier:
        candidates = list(queryset.filter(email_normalized=identifier.lower()).order_by("pk")[:2])
    else:
        try:
            phone_normalized = normalize_phone(identifier)
        except (TypeError, ValidationError):
            candidates = []
        else:
            candidates = list(queryset.filter(phone_normalized=phone_normalized).order_by("pk")[:2])

    # El teléfono deja de ser identidad: si coincide con más de una cuenta no
    # elegimos una por orden de base de datos. El correo verificado sí es único.
    if len(candidates) != 1:
        check_password(password, _dummy_client_password_hash())
        return None
    access = candidates[0]
    if not access.check_password(password):
        return None
    return access


@lru_cache(maxsize=1)
def _dummy_client_password_hash():
    """Iguala el coste de la rama sin cuenta sin persistir una credencial real."""

    return make_password("agendasalon-dummy-client-password")


def client_password_fingerprint(access):
    """Identificador opaco que invalida la sesión cuando cambia la contraseña."""

    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        access.password_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def get_session_client_access(request, business):
    access_id = request.session.get(CLIENT_ACCESS_SESSION_KEY)
    if not access_id:
        return None

    last_seen_raw = request.session.get(CLIENT_ACCESS_LAST_SEEN_SESSION_KEY)
    if not last_seen_raw:
        logout_client_access(request)
        return None
    try:
        last_seen = datetime.fromisoformat(last_seen_raw)
    except (TypeError, ValueError):
        logout_client_access(request)
        return None
    if timezone.is_naive(last_seen):
        last_seen = timezone.make_aware(last_seen)
    now = timezone.now()
    if (now - last_seen).total_seconds() > CLIENT_ACCESS_IDLE_SECONDS:
        logout_client_access(request)
        return None

    access = (
        BusinessClientAccess.objects.select_related("business_client", "business")
        .filter(
            id=access_id,
            business=business,
            is_active=True,
            email_verified_at__isnull=False,
            business_client__is_active=True,
        )
        .first()
    )
    session_fingerprint = request.session.get(CLIENT_ACCESS_PASSWORD_SESSION_KEY, "")
    if access is None or not hmac.compare_digest(
        session_fingerprint,
        client_password_fingerprint(access) if access is not None else "",
    ):
        logout_client_access(request)
        access = None
    else:
        request.session[CLIENT_ACCESS_LAST_SEEN_SESSION_KEY] = now.isoformat()
    return access


def login_client_access(request, access):
    request.session.cycle_key()
    request.session[CLIENT_ACCESS_SESSION_KEY] = access.id
    request.session[CLIENT_ACCESS_LAST_SEEN_SESSION_KEY] = timezone.now().isoformat()
    request.session[CLIENT_ACCESS_PASSWORD_SESSION_KEY] = client_password_fingerprint(access)
    access.last_login_at = timezone.now()
    access.save(update_fields=["last_login_at", "updated_at"])


def logout_client_access(request):
    request.session.pop(CLIENT_ACCESS_SESSION_KEY, None)
    request.session.pop(CLIENT_ACCESS_LAST_SEEN_SESSION_KEY, None)
    request.session.pop(CLIENT_ACCESS_PASSWORD_SESSION_KEY, None)
    request.session.cycle_key()


def invitation_token_digest(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@transaction.atomic
def create_client_access_invitation(*, business, business_client, created_by, now=None):
    now = now or timezone.now()
    business_client = BusinessClient.objects.select_for_update().get(
        pk=business_client.pk,
        business=business,
    )
    if business_client.business_id != business.id:
        raise ValidationError("La ficha no pertenece a este negocio.")
    if not business_client.is_active:
        raise ValidationError("Reactiva la ficha antes de crear una invitación.")
    if not business_client.phone_normalized:
        raise ValidationError(
            "Esta ficha no tiene teléfono propio. Vincula la cuenta online de una persona autorizada."
        )
    if BusinessClientAccess.objects.filter(business_client=business_client).exists():
        raise ValidationError("Esta ficha ya tiene una cuenta online.")

    BusinessClientAccessInvitation.objects.filter(
        business=business,
        business_client=business_client,
        used_at__isnull=True,
        revoked_at__isnull=True,
    ).update(revoked_at=now)

    raw_token = secrets.token_urlsafe(32)
    invitation = BusinessClientAccessInvitation(
        business=business,
        business_client=business_client,
        token_digest=invitation_token_digest(raw_token),
        expires_at=now + timedelta(hours=CLIENT_INVITATION_LIFETIME_HOURS),
        created_by=created_by,
    )
    invitation.full_clean()
    invitation.save()
    return invitation, raw_token


def find_available_invitation(*, invitation_id, raw_token, business=None, now=None, lock=False):
    now = now or timezone.now()
    queryset = BusinessClientAccessInvitation.objects.select_related(
        "business",
        "business_client",
    )
    if lock:
        queryset = queryset.select_for_update()
    filters = {"id": invitation_id}
    if business is not None:
        filters["business"] = business
    invitation = queryset.filter(**filters).first()
    if invitation is None or not invitation.is_available(now):
        return None
    if not hmac.compare_digest(
        invitation.token_digest,
        invitation_token_digest(raw_token),
    ):
        return None
    if BusinessClientAccess.objects.filter(business_client=invitation.business_client).exists():
        return None
    return invitation


def store_invitation_claim(request, invitation):
    request.session.cycle_key()
    request.session[CLIENT_INVITATION_CLAIM_SESSION_KEY] = {
        "invitation_id": str(invitation.id),
        "business_id": invitation.business_id,
        "claimed_at": timezone.now().isoformat(),
    }


def get_claimed_invitation(request, business, now=None, lock=False):
    now = now or timezone.now()
    claim = request.session.get(CLIENT_INVITATION_CLAIM_SESSION_KEY)
    if not isinstance(claim, dict) or claim.get("business_id") != business.id:
        return None
    try:
        claimed_at = datetime.fromisoformat(claim["claimed_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if timezone.is_naive(claimed_at):
        claimed_at = timezone.make_aware(claimed_at)
    if now - claimed_at > timedelta(minutes=30):
        request.session.pop(CLIENT_INVITATION_CLAIM_SESSION_KEY, None)
        return None

    queryset = BusinessClientAccessInvitation.objects.select_related(
        "business",
        "business_client",
    )
    if lock:
        queryset = queryset.select_for_update()
    invitation = queryset.filter(
        id=claim.get("invitation_id"),
        business=business,
    ).first()
    if invitation is None or not invitation.is_available(now):
        return None
    if BusinessClientAccess.objects.filter(business_client=invitation.business_client).exists():
        return None
    return invitation


@transaction.atomic
def activate_claimed_invitation(*, request, business, email, now=None):
    now = now or timezone.now()
    invitation = get_claimed_invitation(request, business, now=now, lock=True)
    if invitation is None:
        raise ValidationError("Esta invitación ya no está disponible.")

    access = BusinessClientAccess(
        business=business,
        business_client=invitation.business_client,
        phone=invitation.business_client.phone,
        email=email,
        is_active=True,
    )
    access.set_password(None)
    access.full_clean()
    access.save()
    ensure_self_booking_grant(access)
    invitation.used_at = now
    invitation.save(update_fields=["used_at"])
    request.session.pop(CLIENT_INVITATION_CLAIM_SESSION_KEY, None)
    return access, invitation


@transaction.atomic
def revoke_client_access_invitation(*, invitation, now=None):
    now = now or timezone.now()
    invitation = BusinessClientAccessInvitation.objects.select_for_update().get(pk=invitation.pk)
    if not invitation.is_available(now):
        raise ValidationError("Esta invitación ya no está disponible.")
    invitation.revoked_at = now
    invitation.save(update_fields=["revoked_at"])
    return invitation


@transaction.atomic
def register_client_access(
    *,
    business,
    full_name: str,
    phone: str,
    email: str,
    password: str | None = None,
    email_verified=False,
):
    if email_verified and not password:
        raise ValidationError("Una cuenta ya verificada necesita contraseña.")
    email_normalized = email.strip().lower()
    locked_business = Business.objects.select_for_update().get(pk=business.pk)
    now = timezone.now()
    _purge_expired_public_registrations_for_locked_business(
        locked_business,
        now=now,
        email_normalized=email_normalized,
    )
    if BusinessClientAccess.objects.filter(
        business=locked_business,
        email_normalized=email_normalized,
    ).exists():
        raise ValidationError(PUBLIC_REGISTRATION_UNAVAILABLE_MESSAGE)

    try:
        # El alta pública siempre crea una ficha nueva. Nunca reclama ni modifica
        # una ficha profesional a partir de un teléfono, que es solo contacto.
        with transaction.atomic():
            client = BusinessClient(
                business=locked_business,
                full_name=full_name,
                phone=phone,
                email=email,
                source=BusinessClient.Source.OTHER,
                is_active=email_verified,
                internal_notes="Ficha creada desde registro online de cliente.",
            )
            client.full_clean()
            client.save()

            access = BusinessClientAccess(
                business=locked_business,
                business_client=client,
                phone=phone,
                email=email,
                email_verified_at=now if email_verified else None,
                is_active=True,
                is_pending_public_registration=not email_verified,
                public_registration_expires_at=(
                    None if email_verified else public_registration_expiry(now=now)
                ),
            )
            access.set_password(password if email_verified else None)
            access.full_clean()
            access.save()
    except (IntegrityError, ValidationError) as exc:
        raise ValidationError(PUBLIC_REGISTRATION_UNAVAILABLE_MESSAGE) from exc
    ensure_self_booking_grant(access)
    return access


@transaction.atomic
def lock_pending_public_registration_for_resend(*, access, now=None):
    """Bloquea una alta vigente antes de reencolar y decidir si renueva."""

    now = now or timezone.now()
    locked_business = Business.objects.select_for_update().get(pk=access.business_id)
    client_id = (
        BusinessClientAccess.objects.filter(
            pk=access.pk,
            business=locked_business,
        )
        .values_list("business_client_id", flat=True)
        .first()
    )
    if client_id is None:
        return None
    client = BusinessClient.objects.select_for_update().get(
        pk=client_id,
        business=locked_business,
    )
    legacy_cutoff = now - timedelta(seconds=PUBLIC_REGISTRATION_RETENTION_SECONDS)
    locked_access = (
        BusinessClientAccess.objects.select_for_update()
        .filter(
            pk=access.pk,
            business=locked_business,
            business_client=client,
            email_verified_at__isnull=True,
            is_active=True,
            is_pending_public_registration=True,
        )
        .filter(
            models.Q(public_registration_expires_at__gt=now)
            | models.Q(
                public_registration_expires_at__isnull=True,
                created_at__gt=legacy_cutoff,
            )
        )
        .first()
    )
    if locked_access is None or client.is_active:
        return None
    return locked_access


@transaction.atomic
def create_or_reuse_professional_client(
    *,
    business,
    full_name: str,
    phone: str,
    email: str = "",
    internal_notes: str = "",
):
    phone_normalized = normalize_phone(phone) if (phone or "").strip() else ""
    name_normalized = normalize_search_text(full_name)

    client = (
        BusinessClient.objects.filter(
            business=business,
            phone_normalized=phone_normalized,
            full_name_normalized=name_normalized,
            is_active=True,
            source=BusinessClient.Source.PROFESSIONAL,
        )
        .order_by("pk")
        .first()
        if phone_normalized
        else None
    )
    if client is not None:
        return client, False

    client = BusinessClient(
        business=business,
        full_name=full_name,
        phone=phone,
        email=email,
        source=BusinessClient.Source.PROFESSIONAL,
        internal_notes=internal_notes,
    )
    client.full_clean()
    client.save()
    return client, True


@transaction.atomic
def update_professional_client(*, client, full_name, phone, email="", internal_notes=""):
    from apps.businesses.models import Business

    Business.objects.select_for_update().get(pk=client.business_id)
    client = BusinessClient.objects.select_for_update().get(
        pk=client.pk,
        business_id=client.business_id,
    )
    phone_normalized = normalize_phone(phone) if (phone or "").strip() else ""
    name_normalized = normalize_search_text(full_name)
    duplicate_client = BusinessClient.objects.none()
    if phone_normalized and client.source == BusinessClient.Source.PROFESSIONAL:
        duplicate_client = BusinessClient.objects.filter(
            business=client.business,
            phone_normalized=phone_normalized,
            full_name_normalized=name_normalized,
            is_active=True,
            source=BusinessClient.Source.PROFESSIONAL,
        ).exclude(pk=client.pk)
    if duplicate_client.exists():
        raise ValidationError("Ya existe una ficha activa con ese nombre y teléfono.")

    access = BusinessClientAccess.objects.select_for_update().filter(business_client=client).first()
    access_to_verify = None
    if access is not None:
        if not phone_normalized:
            raise ValidationError("Una ficha con cuenta online activa debe conservar su teléfono.")

        requested_email = (email or "").strip().lower()
        current_email_normalized = (access.email_normalized or "").strip().lower()
        email_changed = requested_email != current_email_normalized
        if email_changed:
            if not requested_email:
                raise ValidationError(
                    "Una ficha con cuenta online debe conservar su correo electrónico."
                )
            if not access.is_active or not client.is_active:
                raise ValidationError(
                    "Reactiva la ficha y la cuenta online antes de cambiar el correo."
                )
            duplicate_access = (
                BusinessClientAccess.objects.select_for_update()
                .filter(
                    business=client.business,
                    email_normalized=requested_email,
                )
                .exclude(pk=access.pk)
            )
            if duplicate_access.exists():
                raise ValidationError(
                    "Ese correo ya está vinculado a otra cuenta online de este negocio."
                )
            canonical_email = requested_email
        else:
            canonical_email = (access.email or "").strip()
    else:
        email_changed = False
        canonical_email = (email or "").strip()

    client.full_name = full_name.strip()
    client.phone = phone
    client.email = canonical_email
    client.internal_notes = (internal_notes or "").strip()
    client.full_clean()
    client.save()

    if access is not None:
        access_update_fields = []
        if access.phone_normalized != phone_normalized:
            access.phone = phone
            access_update_fields.extend(["phone", "phone_normalized"])
        if email_changed:
            access.email = canonical_email
            access.email_verified_at = None
            access.set_password(None)
            access_update_fields.extend(
                [
                    "email",
                    "email_normalized",
                    "email_verified_at",
                    "password_hash",
                ]
            )
            access_to_verify = access
        if access_update_fields:
            access.full_clean()
            try:
                with transaction.atomic():
                    access.save(update_fields=[*access_update_fields, "updated_at"])
            except IntegrityError as exc:
                raise ValidationError(
                    "Ese correo ya está vinculado a otra cuenta online de este negocio."
                ) from exc
    return client, access_to_verify


@transaction.atomic
def set_professional_client_active(*, client, is_active, now=None):
    if client.is_active == is_active:
        return client

    if not is_active:
        now = now or timezone.now()
        if client.appointments.filter(
            status="confirmada",
            ends_at__gt=now,
        ).exists():
            raise ValidationError(
                "Esta ficha tiene citas confirmadas pendientes. Complétalas o cancélalas antes de pausarla."
            )

    client.is_active = is_active
    client.full_clean()
    client.save(
        update_fields=["is_active", "full_name_normalized", "phone_normalized", "updated_at"]
    )
    if not is_active:
        linked_contacts = BusinessClientAuthorizedContact.objects.filter(
            linked_business_client=client,
            is_active=True,
        )
        BusinessClientAccessGrant.objects.filter(
            authorized_contact__in=linked_contacts,
        ).update(is_active=False)
        linked_contacts.update(is_active=False, is_primary_contact=False)
    return client


@transaction.atomic
def save_authorized_contact(
    *,
    business,
    business_client,
    linked_business_client,
    full_name,
    phone,
    relationship_label,
    is_primary_contact,
    notes="",
    allow_online_booking=False,
    contact=None,
):
    if business_client.business_id != business.id:
        raise ValidationError("La ficha no pertenece a este negocio.")

    if contact is None:
        contact = BusinessClientAuthorizedContact(
            business=business,
            business_client=business_client,
            is_active=True,
        )
    elif contact.business_id != business.id or contact.business_client_id != business_client.id:
        raise ValidationError("La persona autorizada no pertenece a esta ficha.")

    if is_primary_contact:
        BusinessClientAuthorizedContact.objects.filter(
            business_client=business_client,
            is_active=True,
            is_primary_contact=True,
        ).exclude(pk=contact.pk).update(is_primary_contact=False)

    contact.linked_business_client = linked_business_client
    contact.full_name = (
        linked_business_client.full_name if linked_business_client else full_name.strip()
    )
    contact.phone = linked_business_client.phone if linked_business_client else phone
    contact.relationship_label = relationship_label
    contact.is_primary_contact = is_primary_contact
    contact.notes = (notes or "").strip()
    contact.full_clean()
    contact.save()
    linked_grants = BusinessClientAccessGrant.objects.filter(authorized_contact=contact)
    linked_grants.update(is_active=False)
    if linked_business_client and allow_online_booking:
        access = BusinessClientAccess.objects.filter(
            business=business,
            business_client=linked_business_client,
            is_active=True,
        ).first()
        if access is None:
            raise ValidationError("La persona seleccionada no tiene una cuenta online activa.")
        grant, _ = BusinessClientAccessGrant.objects.update_or_create(
            access=access,
            business_client=business_client,
            defaults={
                "business": business,
                "authorized_contact": contact,
                "relationship_label": contact.relationship_label,
                "is_active": True,
            },
        )
        grant.full_clean()
        grant.save()
    return contact


@transaction.atomic
def set_authorized_contact_active(*, contact, is_active):
    if contact.is_active == is_active:
        return contact, False

    demoted_from_primary = False
    if is_active and contact.is_primary_contact:
        has_other_primary = (
            BusinessClientAuthorizedContact.objects.filter(
                business_client=contact.business_client,
                is_active=True,
                is_primary_contact=True,
            )
            .exclude(pk=contact.pk)
            .exists()
        )
        if has_other_primary:
            contact.is_primary_contact = False
            demoted_from_primary = True

    contact.is_active = is_active
    contact.full_clean()
    contact.save(
        update_fields=["is_active", "is_primary_contact", "phone_normalized", "updated_at"]
    )
    return contact, demoted_from_primary


@transaction.atomic
def toggle_contact_online_booking(*, contact):
    if not contact.is_active:
        raise ValidationError("Reactiva primero a esta persona autorizada.")

    if contact.linked_business_client_id is None:
        raise ValidationError("Vincula primero esta persona con una ficha de cliente registrada.")

    access = BusinessClientAccess.objects.filter(
        business=contact.business,
        business_client=contact.linked_business_client,
        is_active=True,
        business_client__is_active=True,
    ).first()
    if access is None:
        raise ValidationError(
            "Esa persona todavía no tiene una cuenta online activa en este negocio."
        )

    grant, created = BusinessClientAccessGrant.objects.get_or_create(
        business=contact.business,
        access=access,
        business_client=contact.business_client,
        defaults={
            "authorized_contact": contact,
            "relationship_label": contact.relationship_label,
            "is_active": True,
        },
    )
    if not created:
        grant.authorized_contact = contact
        grant.relationship_label = contact.relationship_label
        grant.is_active = not grant.is_active
        grant.full_clean()
        grant.save(
            update_fields=[
                "authorized_contact",
                "relationship_label",
                "is_active",
                "updated_at",
            ]
        )
    return grant


@transaction.atomic
def set_client_access_active(*, access, is_active):
    if access.is_active == is_active:
        return access
    if is_active and not access.business_client.is_active:
        raise ValidationError("Reactiva primero la ficha del cliente.")

    access.is_active = is_active
    access.full_clean()
    access.save(update_fields=["is_active", "phone_normalized", "updated_at"])
    return access

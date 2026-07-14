import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from functools import lru_cache

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db import models
from django.utils import timezone

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
CLIENT_INVITATION_CLAIM_SESSION_KEY = "business_client_invitation_claim"
CLIENT_ACCESS_IDLE_SECONDS = 60 * 60
CLIENT_INVITATION_LIFETIME_HOURS = 24
PUBLIC_REGISTRATION_UNAVAILABLE_MESSAGE = (
    "No podemos crear una cuenta con esos datos. "
    "Contacta con el negocio para activar tu acceso."
)


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
    return BusinessClient.objects.filter(
        business=access.business,
        is_active=True,
    ).filter(
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
    ).distinct().order_by("full_name", "pk")


def get_bookable_client(access, client_id):
    return get_bookable_clients(access).filter(pk=client_id).first()


def authenticate_client_access(*, business, phone: str, password: str):
    phone_normalized = normalize_phone(phone)
    access = (
        BusinessClientAccess.objects.select_related("business_client", "business")
        .filter(
            business=business,
            phone_normalized=phone_normalized,
            is_active=True,
            email_verified_at__isnull=False,
            business_client__is_active=True,
        )
        .first()
    )
    if access is None:
        check_password(password, _dummy_client_password_hash())
        return None
    if not access.check_password(password):
        return None
    return access


@lru_cache(maxsize=1)
def _dummy_client_password_hash():
    """Iguala el coste de la rama sin cuenta sin persistir una credencial real."""

    return make_password("agendasalon-dummy-client-password")


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
            business_client__is_active=True,
        )
        .first()
    )
    if access is None:
        logout_client_access(request)
    else:
        request.session[CLIENT_ACCESS_LAST_SEEN_SESSION_KEY] = now.isoformat()
    return access


def login_client_access(request, access):
    request.session.cycle_key()
    request.session[CLIENT_ACCESS_SESSION_KEY] = access.id
    request.session[CLIENT_ACCESS_LAST_SEEN_SESSION_KEY] = timezone.now().isoformat()
    access.last_login_at = timezone.now()
    access.save(update_fields=["last_login_at", "updated_at"])


def logout_client_access(request):
    request.session.pop(CLIENT_ACCESS_SESSION_KEY, None)
    request.session.pop(CLIENT_ACCESS_LAST_SEEN_SESSION_KEY, None)
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
def activate_claimed_invitation(*, request, business, email, password, now=None):
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
    access.set_password(password)
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
    *, business, full_name: str, phone: str, email: str, password: str, email_verified=False
):
    phone_normalized = normalize_phone(phone)
    if BusinessClientAccess.objects.filter(
        business=business,
        phone_normalized=phone_normalized,
    ).exists():
        raise ValidationError(PUBLIC_REGISTRATION_UNAVAILABLE_MESSAGE)

    if BusinessClient.objects.filter(
        business=business,
        phone_normalized=phone_normalized,
    ).exists():
        raise ValidationError(PUBLIC_REGISTRATION_UNAVAILABLE_MESSAGE)

    email_normalized = email.strip().lower()
    if BusinessClientAccess.objects.filter(
        business=business,
        email_normalized=email_normalized,
    ).exists():
        raise ValidationError(PUBLIC_REGISTRATION_UNAVAILABLE_MESSAGE)

    client = BusinessClient(
        business=business,
        full_name=full_name,
        phone=phone,
        email=email,
        source=BusinessClient.Source.OTHER,
        internal_notes="Ficha creada desde registro online de cliente.",
    )
    client.full_clean()
    client.save()

    access = BusinessClientAccess(
        business=business,
        business_client=client,
        phone=phone,
        email=email,
        email_verified_at=timezone.now() if email_verified else None,
        is_active=True,
    )
    access.set_password(password)
    access.full_clean()
    access.save()
    ensure_self_booking_grant(access)
    return access


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
    phone_normalized = normalize_phone(phone) if (phone or "").strip() else ""
    name_normalized = normalize_search_text(full_name)
    duplicate_client = BusinessClient.objects.none()
    if phone_normalized:
        duplicate_client = BusinessClient.objects.filter(
            business=client.business,
            phone_normalized=phone_normalized,
            full_name_normalized=name_normalized,
            is_active=True,
        ).exclude(pk=client.pk)
    if duplicate_client.exists():
        raise ValidationError("Ya existe una ficha activa con ese nombre y teléfono.")

    access = getattr(client, "access", None)
    if access is not None:
        if not phone_normalized:
            raise ValidationError("Una ficha con cuenta online activa debe conservar su teléfono.")
        duplicate_access = BusinessClientAccess.objects.filter(
            business=client.business,
            phone_normalized=phone_normalized,
        ).exclude(pk=access.pk)
        if duplicate_access.exists():
            raise ValidationError("Ese teléfono ya está asociado a otra cuenta online.")

    client.full_name = full_name.strip()
    client.phone = phone
    client.email = (email or "").strip()
    client.internal_notes = (internal_notes or "").strip()
    client.full_clean()
    client.save()

    if access is not None and access.phone_normalized != phone_normalized:
        access.phone = phone
        access.full_clean()
        access.save(update_fields=["phone", "phone_normalized", "updated_at"])
    return client


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
    client.save(update_fields=["is_active", "full_name_normalized", "phone_normalized", "updated_at"])
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
        has_other_primary = BusinessClientAuthorizedContact.objects.filter(
            business_client=contact.business_client,
            is_active=True,
            is_primary_contact=True,
        ).exclude(pk=contact.pk).exists()
        if has_other_primary:
            contact.is_primary_contact = False
            demoted_from_primary = True

    contact.is_active = is_active
    contact.full_clean()
    contact.save(update_fields=["is_active", "is_primary_contact", "phone_normalized", "updated_at"])
    return contact, demoted_from_primary


@transaction.atomic
def toggle_contact_online_booking(*, contact):
    if not contact.is_active:
        raise ValidationError("Reactiva primero a esta persona autorizada.")

    if contact.linked_business_client_id is None:
        raise ValidationError(
            "Vincula primero esta persona con una ficha de cliente registrada."
        )

    access = BusinessClientAccess.objects.filter(
        business=contact.business,
        business_client=contact.linked_business_client,
        is_active=True,
        business_client__is_active=True,
    ).first()
    if access is None:
        raise ValidationError(
            "Ese teléfono todavía no tiene una cuenta online activa en este negocio."
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

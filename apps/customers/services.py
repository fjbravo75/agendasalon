from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.phone import normalize_phone
from apps.core.text import normalize_search_text
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAuthorizedContact,
)


CLIENT_ACCESS_SESSION_KEY = "business_client_access_id"


def authenticate_client_access(*, business, phone: str, password: str):
    phone_normalized = normalize_phone(phone)
    access = (
        BusinessClientAccess.objects.select_related("business_client", "business")
        .filter(
            business=business,
            phone_normalized=phone_normalized,
            is_active=True,
            business_client__is_active=True,
        )
        .first()
    )
    if access is None or not access.check_password(password):
        return None
    return access


def get_session_client_access(request, business):
    access_id = request.session.get(CLIENT_ACCESS_SESSION_KEY)
    if not access_id:
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
        request.session.pop(CLIENT_ACCESS_SESSION_KEY, None)
    return access


def login_client_access(request, access):
    request.session[CLIENT_ACCESS_SESSION_KEY] = access.id
    access.last_login_at = timezone.now()
    access.save(update_fields=["last_login_at", "updated_at"])


def logout_client_access(request):
    request.session.pop(CLIENT_ACCESS_SESSION_KEY, None)


@transaction.atomic
def register_client_access(*, business, full_name: str, phone: str, password: str):
    phone_normalized = normalize_phone(phone)
    if BusinessClientAccess.objects.filter(
        business=business,
        phone_normalized=phone_normalized,
    ).exists():
        raise ValidationError("Ya existe una cuenta cliente con ese teléfono.")

    client = (
        BusinessClient.objects.filter(
            business=business,
            phone_normalized=phone_normalized,
            is_active=True,
        )
        .order_by("full_name", "pk")
        .first()
    )
    if client is None:
        client = BusinessClient(
            business=business,
            full_name=full_name,
            phone=phone,
            source=BusinessClient.Source.OTHER,
            internal_notes="Ficha creada desde registro online de cliente.",
        )
        client.full_clean()
        client.save()

    access = BusinessClientAccess(
        business=business,
        business_client=client,
        phone=phone,
        is_active=True,
    )
    access.set_password(password)
    access.full_clean()
    access.save()
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
    phone_normalized = normalize_phone(phone)
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
    phone_normalized = normalize_phone(phone)
    name_normalized = normalize_search_text(full_name)
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
    return client


@transaction.atomic
def save_authorized_contact(
    *,
    business,
    business_client,
    full_name,
    phone,
    relationship_label,
    is_primary_contact,
    notes="",
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

    contact.full_name = full_name.strip()
    contact.phone = phone
    contact.relationship_label = relationship_label
    contact.is_primary_contact = is_primary_contact
    contact.notes = (notes or "").strip()
    contact.full_clean()
    contact.save()
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
def set_client_access_active(*, access, is_active):
    if access.is_active == is_active:
        return access
    if is_active and not access.business_client.is_active:
        raise ValidationError("Reactiva primero la ficha del cliente.")

    access.is_active = is_active
    access.full_clean()
    access.save(update_fields=["is_active", "phone_normalized", "updated_at"])
    return access

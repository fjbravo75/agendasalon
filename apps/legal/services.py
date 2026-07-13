from django.conf import settings
from django.db import transaction

from apps.legal.models import (
    BusinessLegalProfile,
    CustomerPrivacyEvidence,
    LegalAcceptance,
    LegalDocument,
)


PROFESSIONAL_DOCUMENT_ACTIONS = {
    LegalDocument.Kind.TERMS: LegalAcceptance.Action.ACCEPTED,
    LegalDocument.Kind.PLATFORM_PRIVACY: LegalAcceptance.Action.ACKNOWLEDGED,
    LegalDocument.Kind.DATA_PROCESSING: LegalAcceptance.Action.ACCEPTED,
}


def get_active_document(kind):
    return LegalDocument.objects.filter(kind=kind, is_active=True).first()


def get_public_legal_documents():
    kinds = (
        LegalDocument.Kind.LEGAL_NOTICE,
        LegalDocument.Kind.PLATFORM_PRIVACY,
        LegalDocument.Kind.TERMS,
        LegalDocument.Kind.DATA_PROCESSING,
        LegalDocument.Kind.COOKIES,
    )
    documents = LegalDocument.objects.filter(kind__in=kinds, is_active=True)
    by_kind = {document.kind: document for document in documents}
    return tuple(by_kind[kind] for kind in kinds if kind in by_kind)


def platform_legal_context():
    return {
        "legal_name": settings.AGENDA_PLATFORM_LEGAL_NAME,
        "tax_identifier": settings.AGENDA_PLATFORM_TAX_ID,
        "registered_address": settings.AGENDA_PLATFORM_LEGAL_ADDRESS,
        "privacy_email": settings.AGENDA_PLATFORM_PRIVACY_EMAIL,
        "website": settings.AGENDA_PLATFORM_WEBSITE,
        "is_demo": settings.AGENDA_PLATFORM_LEGAL_DEMO,
    }


def business_legal_snapshot(business):
    profile = BusinessLegalProfile.objects.filter(business=business).first()
    if profile is not None:
        snapshot = profile.snapshot()
        snapshot["is_complete"] = profile.is_complete
        return snapshot

    address = ", ".join(
        part for part in (business.address, business.city, business.province) if part
    )
    return {
        "legal_name": business.commercial_name,
        "tax_identifier": "",
        "registered_address": address,
        "privacy_email": business.public_email or settings.AGENDA_PLATFORM_PRIVACY_EMAIL,
        "rights_contact_name": "",
        "retention_criteria": (
            "Durante la relación con el salón y, después, durante los plazos necesarios "
            "para atender posibles responsabilidades."
        ),
        "is_complete": False,
    }


def business_legal_status_map(businesses):
    businesses = tuple(businesses)
    business_ids = [business.pk for business in businesses]
    profiles = {
        profile.business_id: profile
        for profile in BusinessLegalProfile.objects.filter(business_id__in=business_ids)
    }
    documents = {
        document.kind: document
        for document in LegalDocument.objects.filter(
            kind__in=PROFESSIONAL_DOCUMENT_ACTIONS,
            is_active=True,
        )
    }
    acceptances_by_business = {business_id: {} for business_id in business_ids}
    for acceptance in LegalAcceptance.objects.filter(
            business_id__in=business_ids,
            actor_user__isnull=False,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
            document_id__in=[document.pk for document in documents.values()],
        ).select_related("actor_user", "document").order_by("business_id", "document_id", "-accepted_at", "-pk"):
        acceptances_by_business[acceptance.business_id].setdefault(
            acceptance.document_id,
            acceptance,
        )

    statuses = {}
    for business in businesses:
        profile = profiles.get(business.pk)
        if profile is not None:
            snapshot = profile.snapshot()
            snapshot["is_complete"] = profile.is_complete
        else:
            address = ", ".join(
                part for part in (business.address, business.city, business.province) if part
            )
            snapshot = {
                "legal_name": business.commercial_name,
                "tax_identifier": "",
                "registered_address": address,
                "privacy_email": business.public_email or settings.AGENDA_PLATFORM_PRIVACY_EMAIL,
                "rights_contact_name": "",
                "retention_criteria": (
                    "Durante la relación con el salón y, después, durante los plazos necesarios "
                    "para atender posibles responsabilidades."
                ),
                "is_complete": False,
            }
        if not business.legal_compliance_enabled:
            statuses[business.pk] = {
                "is_current": True,
                "status": "disabled",
                "label": "Control legal no requerido",
                "missing_kinds": (),
                "profile_complete": True,
                "snapshot": snapshot,
                "document_rows": (),
                "latest_acceptance_at": None,
            }
            continue

        acceptances = acceptances_by_business[business.pk]
        statuses[business.pk] = _compose_business_legal_status(
            snapshot=snapshot,
            documents=documents,
            acceptances=acceptances,
        )
    return statuses


def _compose_business_legal_status(*, snapshot, documents, acceptances):
    document_rows = []
    missing_kinds = []
    for kind, required_action in PROFESSIONAL_DOCUMENT_ACTIONS.items():
        document = documents.get(kind)
        acceptance = acceptances.get(document.pk) if document else None
        is_current = bool(
            document
            and acceptance
            and acceptance.action == required_action
            and (
                kind != LegalDocument.Kind.DATA_PROCESSING
                or acceptance.authority_declared
            )
        )
        if not is_current:
            missing_kinds.append(kind)
        document_rows.append(
            {
                "kind": kind,
                "label": LegalDocument.Kind(kind).label,
                "document": document,
                "acceptance": acceptance,
                "is_current": is_current,
            }
        )
    profile_complete = snapshot["is_complete"]
    latest_acceptance_at = max(
        (row["acceptance"].accepted_at for row in document_rows if row["acceptance"]),
        default=None,
    )
    if not profile_complete:
        status = "pending_profile"
        label = "Datos del responsable pendientes"
    elif missing_kinds:
        status = "pending_documents"
        label = "Documentación pendiente"
    else:
        status = "current"
        label = "Documentación vigente"
    return {
        "is_current": profile_complete and not missing_kinds,
        "status": status,
        "label": label,
        "missing_kinds": tuple(missing_kinds),
        "profile_complete": profile_complete,
        "snapshot": snapshot,
        "document_rows": tuple(document_rows),
        "latest_acceptance_at": latest_acceptance_at,
    }


def business_legal_status(business):
    return business_legal_status_map((business,))[business.pk]


def professional_legal_status(user, business):
    """Compatibilidad con el flujo profesional: el cumplimiento pertenece al negocio."""

    return business_legal_status(business)


@transaction.atomic
def accept_professional_legal_documents(*, user, business, profile_data):
    profile, _ = BusinessLegalProfile.objects.update_or_create(
        business=business,
        defaults=profile_data,
    )
    profile.full_clean()
    profile.save()
    snapshot = profile.snapshot()

    acceptances = []
    for kind, action in PROFESSIONAL_DOCUMENT_ACTIONS.items():
        document = get_active_document(kind)
        if document is None:
            raise LegalDocument.DoesNotExist(f"No hay un documento vigente para {kind}.")
        acceptance, _ = LegalAcceptance.objects.update_or_create(
            document=document,
            business=business,
            actor_user=user,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
            defaults={
                "client_access": None,
                "action": action,
                "document_hash_snapshot": document.content_hash,
                "legal_context_snapshot": snapshot,
                "authority_declared": kind == LegalDocument.Kind.DATA_PROCESSING,
            },
        )
        acceptance.full_clean()
        acceptance.save()
        acceptances.append(acceptance)
    return profile, tuple(acceptances)


def customer_privacy_status(business_client):
    business = business_client.business
    if not business.legal_compliance_enabled:
        return {
            "is_current": True,
            "label": "Control legal no requerido",
            "document": None,
            "evidence": None,
            "history": (),
        }
    document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
    if document is None:
        return {
            "is_current": False,
            "label": "Política no disponible",
            "document": None,
            "evidence": None,
            "history": (),
        }
    history = tuple(
        CustomerPrivacyEvidence.objects.filter(
            business=business,
            business_client=business_client,
        ).select_related("document", "client_access", "recorded_by")[:12]
    )
    evidence = next((item for item in history if item.document_id == document.pk), None)
    return {
        "is_current": evidence is not None,
        "label": "Información vigente" if evidence else "Información pendiente",
        "document": document,
        "evidence": evidence,
        "history": history,
    }


def client_privacy_is_current(client_access):
    return customer_privacy_status(client_access.business_client)["is_current"]


CONTEXT_TO_CUSTOMER_CHANNEL = {
    LegalAcceptance.Context.CLIENT_REGISTRATION: CustomerPrivacyEvidence.Channel.ONLINE_REGISTRATION,
    LegalAcceptance.Context.CLIENT_INVITATION: CustomerPrivacyEvidence.Channel.CLIENT_INVITATION,
    LegalAcceptance.Context.BOOKING_CONFIRMATION: CustomerPrivacyEvidence.Channel.BOOKING,
}


@transaction.atomic
def record_customer_privacy_information(
    *,
    business_client,
    recorded_by,
    channel,
    informed_party_type=CustomerPrivacyEvidence.InformedParty.CLIENT,
    informed_party_name_snapshot=None,
):
    document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
    if document is None:
        raise LegalDocument.DoesNotExist("No hay política de privacidad de clientes vigente.")
    evidence = CustomerPrivacyEvidence(
        document=document,
        business=business_client.business,
        business_client=business_client,
        recorded_by=recorded_by,
        event_type=CustomerPrivacyEvidence.EventType.INFORMATION_PROVIDED,
        channel=channel,
        informed_party_type=informed_party_type,
        informed_party_name_snapshot=(
            informed_party_name_snapshot or business_client.full_name
        ),
        document_hash_snapshot=document.content_hash,
        legal_context_snapshot=business_legal_snapshot(business_client.business),
    )
    evidence.full_clean()
    evidence.save()
    return evidence

def acknowledge_customer_privacy(*, client_access, context):
    document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
    if document is None:
        raise LegalDocument.DoesNotExist("No hay política de privacidad de clientes vigente.")
    snapshot = business_legal_snapshot(client_access.business)
    acceptance, _ = LegalAcceptance.objects.update_or_create(
        document=document,
        business=client_access.business,
        client_access=client_access,
        context=context,
        defaults={
            "actor_user": None,
            "action": LegalAcceptance.Action.ACKNOWLEDGED,
            "document_hash_snapshot": document.content_hash,
            "legal_context_snapshot": snapshot,
            "authority_declared": False,
        },
    )
    acceptance.full_clean()
    acceptance.save()
    evidence, _ = CustomerPrivacyEvidence.objects.get_or_create(
        document=document,
        business=client_access.business,
        business_client=client_access.business_client,
        client_access=client_access,
        event_type=CustomerPrivacyEvidence.EventType.ACKNOWLEDGED,
        channel=CONTEXT_TO_CUSTOMER_CHANNEL[context],
        defaults={
            "recorded_by": None,
            "informed_party_type": CustomerPrivacyEvidence.InformedParty.CLIENT,
            "informed_party_name_snapshot": client_access.business_client.full_name,
            "document_hash_snapshot": document.content_hash,
            "legal_context_snapshot": snapshot,
        },
    )
    evidence.full_clean()
    evidence.save()
    return evidence


def business_can_collect_personal_data(business):
    return business_legal_status(business)["is_current"]

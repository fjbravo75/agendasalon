from django.conf import settings
from django.db import transaction

from apps.legal.models import BusinessLegalProfile, LegalAcceptance, LegalDocument


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


def professional_legal_status(user, business):
    if not business.legal_compliance_enabled:
        return {"is_current": True, "missing_kinds": (), "profile_complete": True}

    snapshot = business_legal_snapshot(business)
    documents = {
        document.kind: document
        for document in LegalDocument.objects.filter(
            kind__in=PROFESSIONAL_DOCUMENT_ACTIONS,
            is_active=True,
        )
    }
    accepted_document_ids = set(
        LegalAcceptance.objects.filter(
            business=business,
            actor_user=user,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
            document_id__in=[document.pk for document in documents.values()],
        ).values_list("document_id", flat=True)
    )
    missing_kinds = tuple(
        kind
        for kind in PROFESSIONAL_DOCUMENT_ACTIONS
        if kind not in documents or documents[kind].pk not in accepted_document_ids
    )
    profile_complete = snapshot["is_complete"]
    return {
        "is_current": profile_complete and not missing_kinds,
        "missing_kinds": missing_kinds,
        "profile_complete": profile_complete,
    }


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


def client_privacy_is_current(client_access):
    business = client_access.business
    if not business.legal_compliance_enabled:
        return True
    document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
    if document is None:
        return False
    return LegalAcceptance.objects.filter(
        document=document,
        business=business,
        client_access=client_access,
    ).exists()

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
    return acceptance


def business_can_collect_personal_data(business):
    if not business.legal_compliance_enabled:
        return True
    profile = BusinessLegalProfile.objects.filter(business=business).first()
    if profile is None or not profile.is_complete:
        return False
    document = get_active_document(LegalDocument.Kind.DATA_PROCESSING)
    if document is None:
        return False
    return LegalAcceptance.objects.filter(
        document=document,
        business=business,
        actor_user__isnull=False,
        context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
        authority_declared=True,
    ).exists()

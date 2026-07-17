import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.legal.models import (
    BusinessLegalProfile,
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)
from apps.legal.presentations import (
    LegalPresentationScope,
    resolve_legal_presentation,
)


PROFESSIONAL_DOCUMENT_ACTIONS = {
    LegalDocument.Kind.TERMS: LegalAcceptance.Action.ACCEPTED,
    LegalDocument.Kind.PLATFORM_PRIVACY: LegalAcceptance.Action.ACKNOWLEDGED,
    LegalDocument.Kind.DATA_PROCESSING: LegalAcceptance.Action.ACCEPTED,
}
_DOCUMENT_NOT_PROVIDED = object()
EVENT_FINGERPRINT_COLLISION_MESSAGE = (
    "No podemos reutilizar esta confirmación con otros datos. "
    "Revisa el formulario y vuelve a empezar."
)


def _event_values_match(event, values, *, ignored_fields=()):
    for field_name, expected in values.items():
        if field_name in ignored_fields:
            continue
        actual = getattr(event, field_name)
        if hasattr(expected, "pk"):
            if getattr(actual, "pk", None) != expected.pk:
                return False
        elif actual != expected:
            return False
    return True


def _acceptance_action_fingerprint(
    presentation_token,
    *,
    document,
):
    """Identifica un mismo envío sin convertir el contenido legal en secreto."""

    if not presentation_token:
        return None
    payload = {
        "token": str(presentation_token),
        "document_id": document.pk,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _get_or_create_legal_acceptance_event(
    *,
    document,
    business,
    actor_user=None,
    client_access=None,
    action,
    context,
    document_hash_snapshot,
    legal_context_snapshot,
    authority_declared,
    accepted_at,
    action_fingerprint=None,
):
    values = {
        "document": document,
        "business": business,
        "actor_user": actor_user,
        "client_access": client_access,
        "action": action,
        "context": context,
        "document_hash_snapshot": document_hash_snapshot,
        "legal_context_snapshot": legal_context_snapshot,
        "authority_declared": authority_declared,
        "accepted_at": accepted_at,
    }
    if action_fingerprint is not None:
        candidate = LegalAcceptanceEvent(
            action_fingerprint=action_fingerprint,
            **values,
        )
        candidate.full_clean(validate_unique=False)
        event, created = LegalAcceptanceEvent.objects.get_or_create(
            action_fingerprint=action_fingerprint,
            defaults=values,
        )
        if not created and not _event_values_match(
            event,
            values,
            ignored_fields=("accepted_at",),
        ):
            raise ValidationError(EVENT_FINGERPRINT_COLLISION_MESSAGE)
        return event, created
    event = LegalAcceptanceEvent(**values)
    event.full_clean()
    event.save()
    return event, True


def customer_privacy_action_fingerprint(
    action_source,
    *,
    document,
):
    if not action_source:
        return None
    payload = {
        "source": str(action_source),
        "document_id": document.pk,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _get_or_create_customer_privacy_evidence_event(
    *,
    document,
    business,
    business_client,
    client_access=None,
    recorded_by=None,
    event_type,
    channel,
    informed_party_type,
    informed_party_name_snapshot,
    document_hash_snapshot,
    legal_context_snapshot,
    occurred_at,
    action_fingerprint=None,
):
    values = {
        "document": document,
        "business": business,
        "business_client": business_client,
        "client_access": client_access,
        "recorded_by": recorded_by,
        "event_type": event_type,
        "channel": channel,
        "informed_party_type": informed_party_type,
        "informed_party_name_snapshot": informed_party_name_snapshot,
        "document_hash_snapshot": document_hash_snapshot,
        "legal_context_snapshot": legal_context_snapshot,
        "occurred_at": occurred_at,
    }
    if action_fingerprint is not None:
        candidate = CustomerPrivacyEvidenceEvent(
            action_fingerprint=action_fingerprint,
            **values,
        )
        candidate.full_clean(validate_unique=False)
        event, created = CustomerPrivacyEvidenceEvent.objects.get_or_create(
            action_fingerprint=action_fingerprint,
            defaults=values,
        )
        if not created and not _event_values_match(
            event,
            values,
            ignored_fields=("occurred_at",),
        ):
            raise ValidationError(EVENT_FINGERPRINT_COLLISION_MESSAGE)
        return event, created
    event = CustomerPrivacyEvidenceEvent(**values)
    event.full_clean()
    event.save()
    return event, True


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
    accepted_context = {
        "platform": platform_legal_context(),
        "business": {
            key: value for key, value in snapshot.items() if key != "is_complete"
        },
    }
    document_rows = []
    missing_kinds = []
    for kind, required_action in PROFESSIONAL_DOCUMENT_ACTIONS.items():
        document = documents.get(kind)
        acceptance = acceptances.get(document.pk) if document else None
        is_current = bool(
            document
            and acceptance
            and acceptance.action == required_action
            and acceptance.document_hash_snapshot == document.content_hash
            and _professional_acceptance_context_is_current(
                acceptance.legal_context_snapshot,
                accepted_context,
            )
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


def _professional_acceptance_context_is_current(recorded_context, current_context):
    if not isinstance(recorded_context, dict):
        return False
    if "platform" in recorded_context or "business" in recorded_context:
        return recorded_context == current_context
    # Compatibilidad con evidencias previas a P1: conservaban solo la identidad
    # del negocio. Siguen siendo válidas mientras esa parte no haya cambiado.
    return recorded_context == current_context["business"]


def business_legal_status(business):
    return business_legal_status_map((business,))[business.pk]


def professional_legal_status(user, business):
    """Compatibilidad con el flujo profesional: el cumplimiento pertenece al negocio."""

    return business_legal_status(business)


@transaction.atomic
def accept_professional_legal_documents(
    *,
    user,
    business,
    profile_data,
    legal_presentation_token=None,
    action_fingerprint_source=None,
    accepted_at=None,
):
    presented_documents = None
    presented_platform_context = platform_legal_context()
    presentation_action_key = action_fingerprint_source
    if legal_presentation_token is not None:
        business = business.__class__.objects.select_for_update().get(pk=business.pk)
        receipt = resolve_legal_presentation(
            legal_presentation_token,
            scope=LegalPresentationScope.PROFESSIONAL_ONBOARDING,
            audience={"business_id": business.pk, "user_id": user.pk},
            required_kinds=PROFESSIONAL_DOCUMENT_ACTIONS,
            legal_context=platform_legal_context(),
        )
        presented_documents = {
            document.kind: document for document in receipt.documents
        }
        presented_platform_context = receipt.legal_context or {}
        presentation_action_key = receipt.receipt_id

    profile, _ = BusinessLegalProfile.objects.update_or_create(
        business=business,
        defaults=profile_data,
    )
    profile.full_clean()
    profile.save()
    snapshot = {
        "platform": presented_platform_context,
        "business": profile.snapshot(),
    }
    accepted_at = accepted_at or timezone.now()

    acceptances = []
    created_any_event = False
    for kind, action in PROFESSIONAL_DOCUMENT_ACTIONS.items():
        document = (
            presented_documents.get(kind)
            if presented_documents is not None
            else get_active_document(kind)
        )
        if document is None:
            raise LegalDocument.DoesNotExist(f"No hay un documento vigente para {kind}.")
        action_fingerprint = _acceptance_action_fingerprint(
            presentation_action_key,
            document=document,
        )
        authority_declared = kind == LegalDocument.Kind.DATA_PROCESSING
        event, event_created = _get_or_create_legal_acceptance_event(
            document=document,
            business=business,
            actor_user=user,
            action=action,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
            document_hash_snapshot=document.content_hash,
            legal_context_snapshot=snapshot,
            authority_declared=authority_declared,
            accepted_at=accepted_at,
            action_fingerprint=action_fingerprint,
        )
        created_any_event = created_any_event or event_created
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
                "authority_declared": authority_declared,
                "accepted_at": event.accepted_at,
            },
        )
        acceptance.accepted_at = event.accepted_at
        acceptance.full_clean()
        acceptance.save()
        acceptances.append(acceptance)
    return profile, tuple(acceptances), created_any_event


def customer_privacy_status(
    business_client,
    *,
    document=_DOCUMENT_NOT_PROVIDED,
):
    business = business_client.business
    history = tuple(
        CustomerPrivacyEvidenceEvent.objects.filter(
            business=business,
            business_client=business_client,
        ).select_related("document", "client_access", "recorded_by")[:12]
    )
    latest_evidence = history[0] if history else None
    if not business.legal_compliance_enabled:
        return {
            "is_current": True,
            "label": "Control legal no requerido",
            "document": None,
            "evidence": None,
            "latest_evidence": latest_evidence,
            "history": history,
        }
    if document is _DOCUMENT_NOT_PROVIDED:
        document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
    if (
        document is None
        or document.kind != LegalDocument.Kind.CUSTOMER_PRIVACY
        or not document.is_active
    ):
        return {
            "is_current": False,
            "label": "Política no disponible",
            "document": None,
            "evidence": None,
            "latest_evidence": latest_evidence,
            "history": history,
        }
    current_legal_context = business_legal_snapshot(business)
    matching_evidence = CustomerPrivacyEvidence.objects.filter(
            business=business,
            business_client=business_client,
            document=document,
            document_hash_snapshot=document.content_hash,
        )
    matching_evidence = matching_evidence.select_related(
        "document",
        "client_access",
        "recorded_by",
    )
    evidence = next(
        (
            item
            for item in matching_evidence
            if item.legal_context_snapshot == current_legal_context
        ),
        None,
    )
    return {
        "is_current": evidence is not None,
        "label": "Información vigente" if evidence else "Información pendiente",
        "document": document,
        "evidence": evidence,
        "latest_evidence": latest_evidence,
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
    document=None,
    legal_context_snapshot=None,
    action_fingerprint_source=None,
    occurred_at=None,
):
    document = document or get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
    if document is None:
        raise LegalDocument.DoesNotExist("No hay política de privacidad de clientes vigente.")
    if document.kind != LegalDocument.Kind.CUSTOMER_PRIVACY or not document.is_active:
        raise LegalDocument.DoesNotExist("No hay política de privacidad de clientes vigente.")
    snapshot = (
        legal_context_snapshot
        if legal_context_snapshot is not None
        else business_legal_snapshot(business_client.business)
    )
    informed_party_name = informed_party_name_snapshot or business_client.full_name
    action_fingerprint = customer_privacy_action_fingerprint(
        action_fingerprint_source,
        document=document,
    )
    event, _ = _get_or_create_customer_privacy_evidence_event(
        document=document,
        business=business_client.business,
        business_client=business_client,
        recorded_by=recorded_by,
        event_type=CustomerPrivacyEvidence.EventType.INFORMATION_PROVIDED,
        channel=channel,
        informed_party_type=informed_party_type,
        informed_party_name_snapshot=informed_party_name,
        document_hash_snapshot=document.content_hash,
        legal_context_snapshot=snapshot,
        occurred_at=occurred_at or timezone.now(),
        action_fingerprint=action_fingerprint,
    )
    existing_projection = CustomerPrivacyEvidence.objects.filter(
        document=document,
        business=business_client.business,
        business_client=business_client,
        recorded_by=recorded_by,
        event_type=CustomerPrivacyEvidence.EventType.INFORMATION_PROVIDED,
        channel=channel,
        occurred_at=event.occurred_at,
    ).first()
    if existing_projection is not None:
        return existing_projection

    evidence = CustomerPrivacyEvidence(
        document=event.document,
        business=event.business,
        business_client=event.business_client,
        client_access=event.client_access,
        recorded_by=event.recorded_by,
        event_type=event.event_type,
        channel=event.channel,
        informed_party_type=event.informed_party_type,
        informed_party_name_snapshot=event.informed_party_name_snapshot,
        document_hash_snapshot=event.document_hash_snapshot,
        legal_context_snapshot=event.legal_context_snapshot,
        occurred_at=event.occurred_at,
    )
    evidence.full_clean()
    evidence.save()
    return evidence

@transaction.atomic
def acknowledge_customer_privacy(
    *,
    client_access,
    context,
    document=None,
    legal_context_snapshot=None,
    action_fingerprint_source=None,
    acknowledged_at=None,
):
    document = document or get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
    if document is None:
        raise LegalDocument.DoesNotExist("No hay política de privacidad de clientes vigente.")
    if document.kind != LegalDocument.Kind.CUSTOMER_PRIVACY or not document.is_active:
        raise LegalDocument.DoesNotExist("No hay política de privacidad de clientes vigente.")
    snapshot = (
        legal_context_snapshot
        if legal_context_snapshot is not None
        else business_legal_snapshot(client_access.business)
    )
    acknowledged_at = acknowledged_at or timezone.now()
    action = LegalAcceptance.Action.ACKNOWLEDGED
    action_fingerprint = _acceptance_action_fingerprint(
        action_fingerprint_source,
        document=document,
    )
    acceptance_event, _ = _get_or_create_legal_acceptance_event(
        document=document,
        business=client_access.business,
        client_access=client_access,
        action=action,
        context=context,
        document_hash_snapshot=document.content_hash,
        legal_context_snapshot=snapshot,
        authority_declared=False,
        accepted_at=acknowledged_at,
        action_fingerprint=action_fingerprint,
    )
    evidence_action_fingerprint = customer_privacy_action_fingerprint(
        action_fingerprint_source,
        document=document,
    )
    privacy_event, _ = _get_or_create_customer_privacy_evidence_event(
        document=document,
        business=client_access.business,
        business_client=client_access.business_client,
        client_access=client_access,
        event_type=CustomerPrivacyEvidence.EventType.ACKNOWLEDGED,
        channel=CONTEXT_TO_CUSTOMER_CHANNEL[context],
        informed_party_type=CustomerPrivacyEvidence.InformedParty.CLIENT,
        informed_party_name_snapshot=client_access.business_client.full_name,
        document_hash_snapshot=document.content_hash,
        legal_context_snapshot=snapshot,
        occurred_at=acceptance_event.accepted_at,
        action_fingerprint=evidence_action_fingerprint,
    )

    acceptance, _ = LegalAcceptance.objects.update_or_create(
        document=document,
        business=client_access.business,
        client_access=client_access,
        context=context,
        defaults={
            "actor_user": None,
            "action": action,
            "document_hash_snapshot": document.content_hash,
            "legal_context_snapshot": snapshot,
            "authority_declared": False,
            "accepted_at": acceptance_event.accepted_at,
        },
    )
    acceptance.accepted_at = acceptance_event.accepted_at
    acceptance.full_clean()
    acceptance.save()
    evidence, _ = CustomerPrivacyEvidence.objects.update_or_create(
        document=document,
        business=client_access.business,
        business_client=client_access.business_client,
        client_access=client_access,
        event_type=CustomerPrivacyEvidence.EventType.ACKNOWLEDGED,
        channel=CONTEXT_TO_CUSTOMER_CHANNEL[context],
        defaults={
            "recorded_by": privacy_event.recorded_by,
            "informed_party_type": privacy_event.informed_party_type,
            "informed_party_name_snapshot": privacy_event.informed_party_name_snapshot,
            "document_hash_snapshot": privacy_event.document_hash_snapshot,
            "legal_context_snapshot": privacy_event.legal_context_snapshot,
            "occurred_at": privacy_event.occurred_at,
        },
    )
    evidence.full_clean()
    evidence.save()
    return evidence


def business_can_collect_personal_data(business):
    return business_legal_status(business)["is_current"]

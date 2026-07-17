from __future__ import annotations

from dataclasses import dataclass
import hmac
import json
import secrets

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.legal.models import LegalDocument


LEGAL_PRESENTATION_FIELD_NAME = "legal_presentation_token"
LEGAL_PRESENTATION_MAX_AGE_SECONDS = 30 * 60
LEGAL_PRESENTATION_SALT = "agendasalon.legal.presentation.v1"
LEGAL_PRESENTATION_CHANGED_MESSAGE = (
    "No podemos confirmar la información legal desde este formulario. "
    "Revísala de nuevo y vuelve a confirmar."
)
LEGAL_PRESENTATION_TRANSACTION_REQUIRED_MESSAGE = (
    "La presentación legal debe resolverse dentro de una transacción atómica "
    "que permanezca abierta hasta guardar la evidencia asociada."
)


class LegalPresentationScope:
    BUSINESS_SIGNUP = "business_signup"
    PROFESSIONAL_ONBOARDING = "professional_onboarding"
    CLIENT_EMAIL_VERIFICATION = "client_email_verification"
    PROFESSIONAL_CLIENT_QUICK = "professional_client_quick"
    PROFESSIONAL_CLIENT_PRIVACY = "professional_client_privacy"
    PUBLIC_BOOKING = "public_booking"


class LegalPresentationError(ValidationError):
    pass


@dataclass(frozen=True)
class LegalPresentationReceipt:
    documents: tuple[LegalDocument, ...]
    legal_context: dict | None
    receipt_id: str

    def document(self, kind: str) -> LegalDocument:
        for document in self.documents:
            if document.kind == kind:
                return document
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE)


def clear_legal_confirmation_fields(form, field_names) -> None:
    """Desmarca confirmaciones ligadas a una presentación que ya no es válida."""

    if not form.is_bound:
        return
    form_data = form.data.copy()
    for field_name in field_names:
        form_data.pop(field_name, None)
        if hasattr(form, "cleaned_data"):
            form.cleaned_data.pop(field_name, None)
    form.data = form_data


def issue_legal_presentation(
    *,
    scope: str,
    audience: dict,
    documents,
    legal_context: dict | None = None,
) -> str:
    received_documents = tuple(documents)
    if not received_documents or any(
        not isinstance(document, LegalDocument)
        or document.pk is None
        or not document.is_active
        for document in received_documents
    ):
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE)
    normalized_documents = tuple(
        sorted(received_documents, key=lambda document: (document.kind, document.pk))
    )

    payload = {
        "v": 1,
        "receipt_id": secrets.token_urlsafe(18),
        "scope": scope,
        "audience": _json_value(audience),
        "documents": [
            {
                "id": document.pk,
                "kind": document.kind,
                "version": document.version,
                "hash": document.content_hash,
            }
            for document in normalized_documents
        ],
        "legal_context": _json_value(legal_context),
    }
    return signing.dumps(payload, salt=LEGAL_PRESENTATION_SALT, compress=True)


def resolve_legal_presentation(
    token: str,
    *,
    scope: str,
    audience: dict,
    required_kinds,
    legal_context: dict | None = None,
) -> LegalPresentationReceipt:
    """Revalida y bloquea la versión legal mostrada antes de guardar evidencia.

    El llamador debe abrir una transacción atómica y mantenerla hasta completar
    las escrituras asociadas. Así, el bloqueo de ``LegalDocument`` no se libera
    entre la revalidación del recibo y el registro de la evidencia legal.
    """

    if not transaction.get_connection().in_atomic_block:
        raise transaction.TransactionManagementError(
            LEGAL_PRESENTATION_TRANSACTION_REQUIRED_MESSAGE
        )

    try:
        payload = signing.loads(
            token,
            salt=LEGAL_PRESENTATION_SALT,
            max_age=LEGAL_PRESENTATION_MAX_AGE_SECONDS,
        )
    except (signing.BadSignature, signing.SignatureExpired, TypeError, ValueError) as exc:
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE) from exc

    if not isinstance(payload, dict):
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE)

    required_kinds = tuple(sorted(tuple(required_kinds)))
    entries = payload.get("documents")
    if (
        payload.get("v") != 1
        or not isinstance(payload.get("receipt_id"), str)
        or not payload["receipt_id"]
        or payload.get("scope") != scope
        or payload.get("audience") != _json_value(audience)
        or payload.get("legal_context") != _json_value(legal_context)
        or not isinstance(entries, list)
        or len(entries) != len(required_kinds)
    ):
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE)

    if any(
        not isinstance(entry, dict) or not isinstance(entry.get("kind"), str)
        for entry in entries
    ):
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE)
    if tuple(sorted(entry["kind"] for entry in entries)) != required_kinds:
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE)

    document_ids = [entry.get("id") for entry in entries]
    if (
        any(type(document_id) is not int for document_id in document_ids)
        or len(set(document_ids)) != len(document_ids)
    ):
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE)

    locked_documents = {
        document.pk: document
        for document in LegalDocument.objects.select_for_update()
        .filter(pk__in=document_ids)
        .order_by("pk")
    }
    resolved_documents = []
    for entry in entries:
        document = locked_documents.get(entry["id"])
        if (
            document is None
            or not document.is_active
            or document.kind != entry.get("kind")
            or document.version != entry.get("version")
            or not _safe_hash_equals(document.content_hash, entry.get("hash"))
        ):
            raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE)
        resolved_documents.append(document)

    return LegalPresentationReceipt(
        documents=tuple(sorted(resolved_documents, key=lambda document: document.kind)),
        legal_context=payload.get("legal_context"),
        receipt_id=payload["receipt_id"],
    )


def _safe_hash_equals(expected: str, received) -> bool:
    if not isinstance(received, str):
        return False
    return hmac.compare_digest(expected, received)


def _json_value(value):
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise LegalPresentationError(LEGAL_PRESENTATION_CHANGED_MESSAGE) from exc

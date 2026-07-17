from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.businesses.activity import record_business_activity
from apps.businesses.models import Business, BusinessActivityEvent
from apps.businesses.services import get_primary_business_for_user
from apps.customers.services import get_session_client_access
from apps.legal.forms import (
    BusinessLegalOnboardingForm,
    DataRightsRequestForm,
    DataRightsResolutionForm,
)
from apps.legal.models import DataRightsRequest, LegalAcceptance, LegalDocument
from apps.legal.presentations import (
    LegalPresentationError,
    LegalPresentationScope,
    clear_legal_confirmation_fields,
    issue_legal_presentation,
    resolve_legal_presentation,
)
from apps.legal.services import (
    accept_professional_legal_documents,
    business_legal_status,
    business_legal_snapshot,
    get_active_document,
    get_public_legal_documents,
    platform_legal_context,
    professional_legal_status,
)


def legal_index(request):
    return render(
        request,
        "legal/index.html",
        {
            "documents": get_public_legal_documents(),
            "legal_context": platform_legal_context(),
        },
    )


def platform_document(request, slug):
    document = get_object_or_404(LegalDocument, slug=slug, is_active=True)
    if document.kind == LegalDocument.Kind.CUSTOMER_PRIVACY:
        return redirect("legal:legal_index")
    return render(
        request,
        "legal/document.html",
        {
            "document": document,
            "legal_context": platform_legal_context(),
        },
    )


def business_privacy(request, slug):
    # La información de privacidad y el ejercicio de derechos no dependen de
    # que el negocio esté aceptando reservas en este momento.
    business = get_object_or_404(Business, slug=slug)
    document = LegalDocument.objects.filter(
        kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
        is_active=True,
    ).first()
    client_access = get_session_client_access(request, business)
    rights_form = DataRightsRequestForm(request.POST or None)

    if request.method == "POST":
        if client_access is None:
            login_url = reverse("customers:client_access", args=[business.slug])
            return redirect(f"{login_url}?next={request.path}")
        if rights_form.is_valid():
            rights_request = rights_form.save(commit=False)
            rights_request.business = business
            rights_request.client_access = client_access
            rights_request.full_clean()
            rights_request.save()
            messages.success(
                request,
                "La solicitud queda registrada. El negocio podrá revisarla desde su área de privacidad.",
            )
            return redirect("legal:business_privacy", slug=business.slug)

    return render(
        request,
        "legal/business_privacy.html",
        {
            "business": business,
            "document": document,
            "business_legal": business_legal_snapshot(business),
            "client_access": client_access,
            "rights_form": rights_form,
            "rights_requests": (
                client_access.data_rights_requests.filter(business=business)[:5]
                if client_access is not None
                else ()
            ),
            "professional_theme": business.professional_theme,
        },
    )


def _safe_professional_onboarding_next_url(request):
    next_url = (
        request.POST.get("next", "")
        if request.method == "POST"
        else request.GET.get("next", "")
    )
    if next_url and "\\" not in next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


@login_required
def professional_onboarding(request):
    if request.user.is_superuser:
        raise PermissionDenied
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    next_url = _safe_professional_onboarding_next_url(request)

    current_status = professional_legal_status(request.user, business)
    profile = getattr(business, "legal_profile", None)
    initial = {
        "legal_name": getattr(profile, "legal_name", "") or business.commercial_name,
        "tax_identifier": getattr(profile, "tax_identifier", ""),
        "registered_address": getattr(profile, "registered_address", "")
        or ", ".join(part for part in (business.address, business.city, business.province) if part),
        "privacy_email": getattr(profile, "privacy_email", "") or business.public_email,
        "rights_contact_name": getattr(profile, "rights_contact_name", ""),
        "retention_criteria": getattr(profile, "retention_criteria", "")
        or (
            "Durante la relación con el salón y, después, durante los plazos necesarios "
            "para atender posibles responsabilidades."
        ),
    }
    onboarding_form = BusinessLegalOnboardingForm(request.POST or None, initial=initial)
    legal_documents_unavailable_message = ""

    onboarding_form_is_valid = (
        onboarding_form.is_valid() if request.method == "POST" else False
    )
    validated_receipt = None
    if request.method == "POST":
        try:
            if onboarding_form_is_valid:
                _, acceptances, created_legal_events = accept_professional_legal_documents(
                    user=request.user,
                    business=business,
                    profile_data=onboarding_form.profile_data(),
                    legal_presentation_token=request.POST.get(
                        "legal_presentation_token",
                        "",
                    ),
                )
            else:
                with transaction.atomic():
                    validated_receipt = resolve_legal_presentation(
                        request.POST.get("legal_presentation_token", ""),
                        scope=LegalPresentationScope.PROFESSIONAL_ONBOARDING,
                        audience={
                            "business_id": business.pk,
                            "user_id": request.user.pk,
                        },
                        required_kinds=(
                            LegalDocument.Kind.PLATFORM_PRIVACY,
                            LegalDocument.Kind.TERMS,
                            LegalDocument.Kind.DATA_PROCESSING,
                        ),
                        legal_context=platform_legal_context(),
                    )
        except (LegalPresentationError, ValidationError) as exc:
            clear_legal_confirmation_fields(
                onboarding_form,
                (
                    "platform_privacy_acknowledged",
                    "terms_accepted",
                    "data_processing_accepted",
                    "authority_declared",
                ),
            )
            onboarding_form.add_error(None, exc)
        else:
            if onboarding_form_is_valid and created_legal_events:
                record_business_activity(
                    business=business,
                    category=BusinessActivityEvent.Category.ACCESS,
                    event_type=BusinessActivityEvent.EventType.LEGAL_DOCUMENTATION_ACCEPTED,
                    origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                    summary="Documentación de privacidad y encargo aceptada por el negocio.",
                    actor=request.user,
                    entity=business,
                    entity_type="business",
                    changes={
                        "document_versions": {
                            acceptance.document.kind: acceptance.document.version
                            for acceptance in acceptances
                        }
                    },
                )
            if onboarding_form_is_valid:
                messages.success(
                    request,
                    "La documentación queda vinculada al negocio y guardada con su versión exacta.",
                )
                if next_url:
                    return redirect(next_url)
                return redirect("legal:professional_center")

    if validated_receipt is not None:
        documents = {
            document.kind: document for document in validated_receipt.documents
        }
        legal_presentation_token = request.POST.get("legal_presentation_token", "")
        legal_documents_available = True
    else:
        documents = {
            kind: get_active_document(kind)
            for kind in (
                LegalDocument.Kind.PLATFORM_PRIVACY,
                LegalDocument.Kind.TERMS,
                LegalDocument.Kind.DATA_PROCESSING,
            )
        }
        legal_documents_available = all(documents.values())
        if legal_documents_available:
            legal_presentation_token = issue_legal_presentation(
                scope=LegalPresentationScope.PROFESSIONAL_ONBOARDING,
                audience={"business_id": business.pk, "user_id": request.user.pk},
                documents=documents.values(),
                legal_context=platform_legal_context(),
            )
        else:
            legal_presentation_token = ""
            legal_documents_unavailable_message = (
                "Ahora mismo no podemos mostrar toda la documentación legal necesaria. "
                "No hemos guardado ningún cambio. Inténtalo de nuevo más tarde."
            )
    return render(
        request,
        "legal/professional_onboarding.html",
        {
            "business": business,
            "onboarding_form": onboarding_form,
            "documents": documents,
            "legal_documents_available": legal_documents_available,
            "legal_presentation_token": legal_presentation_token,
            "legal_documents_unavailable_message": (
                legal_documents_unavailable_message
            ),
            "current_status": current_status,
            "next_url": next_url,
        },
        status=200 if legal_documents_available else 503,
    )


@login_required
def professional_center(request):
    if request.user.is_superuser:
        raise PermissionDenied
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    status = professional_legal_status(request.user, business)
    if not status["is_current"]:
        return redirect("legal:professional_onboarding")

    acceptances = (
        LegalAcceptance.objects.filter(
            business=business,
            actor_user__isnull=False,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
            document__is_active=True,
        )
        .select_related("document", "actor_user")
        .order_by("document__kind")
    )
    rights_requests = tuple(
        business.data_rights_requests.select_related(
            "client_access__business_client"
        )[:20]
    )
    return render(
        request,
        "legal/professional_center.html",
        {
            "business": business,
            "business_legal": business_legal_snapshot(business),
            "legal_status": business_legal_status(business),
            "acceptances": acceptances,
            "rights_requests": rights_requests,
            "rights_rows": tuple(
                {
                    "request": rights_request,
                    "form": DataRightsResolutionForm(instance=rights_request),
                }
                for rights_request in rights_requests
            ),
        },
    )


@login_required
@require_POST
def professional_rights_request_update(request, request_id):
    if request.user.is_superuser:
        raise PermissionDenied
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    rights_request = get_object_or_404(
        DataRightsRequest,
        pk=request_id,
        business=business,
    )
    resolution_form = DataRightsResolutionForm(request.POST, instance=rights_request)
    if resolution_form.is_valid():
        rights_request = resolution_form.save(commit=False)
        rights_request.full_clean()
        rights_request.save()
        messages.success(request, "La solicitud de derechos queda actualizada.")
    else:
        messages.error(request, "Revisa el estado y la nota de gestión.")
    return redirect("legal:professional_center")

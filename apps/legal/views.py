from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
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
    business = get_object_or_404(Business, slug=slug, is_active=True)
    document = get_object_or_404(
        LegalDocument,
        kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
        is_active=True,
    )
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


@login_required
def professional_onboarding(request):
    if request.user.is_superuser:
        return HttpResponseForbidden("El alta legal corresponde al representante del negocio.")
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

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

    if request.method == "POST" and onboarding_form.is_valid():
        _, acceptances = accept_professional_legal_documents(
            user=request.user,
            business=business,
            profile_data=onboarding_form.profile_data(),
        )
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
        messages.success(
            request,
            "La documentación queda vinculada al negocio y guardada con su versión exacta.",
        )
        next_url = request.POST.get("next")
        if next_url and next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect("legal:professional_center")

    documents = {
        kind: get_active_document(kind)
        for kind in (
            LegalDocument.Kind.PLATFORM_PRIVACY,
            LegalDocument.Kind.TERMS,
            LegalDocument.Kind.DATA_PROCESSING,
        )
    }
    return render(
        request,
        "legal/professional_onboarding.html",
        {
            "business": business,
            "onboarding_form": onboarding_form,
            "documents": documents,
            "current_status": current_status,
            "next_url": request.GET.get("next", ""),
        },
    )


@login_required
def professional_center(request):
    if request.user.is_superuser:
        return HttpResponseForbidden("La privacidad de clientes pertenece a cada negocio.")
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
        return HttpResponseForbidden("La solicitud pertenece al negocio responsable.")
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

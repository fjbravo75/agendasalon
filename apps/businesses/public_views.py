from django.shortcuts import redirect, render
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from apps.businesses.forms import BusinessSignupRequestForm
from apps.businesses.models import BusinessSignupRequest
from apps.businesses.services import get_platform_login_image_url
from apps.core.security_throttle import (
    ThrottleLimit,
    phone_throttle_key,
    request_ip,
    reserve_throttle_attempts,
)
from apps.legal.models import LegalDocument
from apps.legal.presentations import (
    LegalPresentationError,
    LegalPresentationScope,
    clear_legal_confirmation_fields,
    issue_legal_presentation,
    resolve_legal_presentation,
)
from apps.legal.services import get_active_document, platform_legal_context


BUSINESS_SIGNUP_THROTTLE_MESSAGE = (
    "Ya hemos recibido varios envíos. Espera antes de volver a intentarlo."
)
BUSINESS_SIGNUP_LEGAL_UNAVAILABLE_MESSAGE = (
    "Ahora mismo no podemos mostrar la información legal necesaria ni registrar "
    "la solicitud. No hemos guardado ningún dato. Inténtalo de nuevo más tarde."
)


@require_http_methods(["GET", "POST"])
def business_signup_request(request):
    form = BusinessSignupRequestForm(request.POST or None)
    response_status = 200
    privacy_document = get_active_document(LegalDocument.Kind.PLATFORM_PRIVACY)
    validated_receipt = None
    legal_unavailable_message = ""

    if request.method == "GET" and privacy_document is None:
        legal_unavailable_message = BUSINESS_SIGNUP_LEGAL_UNAVAILABLE_MESSAGE
        response_status = 503

    if request.method == "POST":
        if privacy_document is None:
            legal_unavailable_message = BUSINESS_SIGNUP_LEGAL_UNAVAILABLE_MESSAGE
            response_status = 503
        else:
            try:
                with transaction.atomic():
                    receipt = resolve_legal_presentation(
                        request.POST.get("legal_presentation_token", ""),
                        scope=LegalPresentationScope.BUSINESS_SIGNUP,
                        audience={"channel": "public"},
                        required_kinds=(LegalDocument.Kind.PLATFORM_PRIVACY,),
                        legal_context=platform_legal_context(),
                    )
                    validated_receipt = receipt
                    privacy_document = receipt.document(
                        LegalDocument.Kind.PLATFORM_PRIVACY
                    )
                    reservation = reserve_throttle_attempts(
                        limits=(
                            ThrottleLimit(
                                scope="business_signup_phone",
                                key=phone_throttle_key(request.POST.get("phone")),
                                limit=3,
                                window_seconds=24 * 60 * 60,
                            ),
                            ThrottleLimit(
                                scope="business_signup_ip",
                                key=request_ip(request),
                                limit=12,
                                window_seconds=60 * 60,
                            ),
                        )
                    )
                    if not reservation.allowed:
                        form.add_error(None, BUSINESS_SIGNUP_THROTTLE_MESSAGE)
                        response_status = 429
                    elif form.is_valid():
                        duplicate_exists = BusinessSignupRequest.objects.filter(
                            normalized_phone=form.normalized_phone,
                            business_name__iexact=form.cleaned_data["business_name"],
                            city__iexact=form.cleaned_data["city"],
                            status__in=BusinessSignupRequest.open_statuses(),
                        ).exists()
                        if not duplicate_exists:
                            signup_request = form.save(commit=False)
                            signup_request.normalized_phone = form.normalized_phone
                            signup_request.privacy_document = privacy_document
                            signup_request.privacy_document_version = privacy_document.version
                            signup_request.privacy_document_hash = privacy_document.content_hash
                            signup_request.privacy_legal_context_snapshot = (
                                receipt.legal_context or {}
                            )
                            signup_request.privacy_acknowledged_at = timezone.now()
                            signup_request.save()
                        return redirect("business_signup_request_success")
            except LegalPresentationError as exc:
                clear_legal_confirmation_fields(
                    form,
                    ("privacy_acknowledged",),
                )
                form.add_error(None, exc)

    if request.method == "POST" and form.errors:
        form.apply_error_accessibility()

    if validated_receipt is not None:
        privacy_document = validated_receipt.document(
            LegalDocument.Kind.PLATFORM_PRIVACY
        )
        legal_presentation_token = request.POST.get("legal_presentation_token", "")
    else:
        privacy_document = get_active_document(LegalDocument.Kind.PLATFORM_PRIVACY)
        legal_presentation_token = (
            issue_legal_presentation(
                scope=LegalPresentationScope.BUSINESS_SIGNUP,
                audience={"channel": "public"},
                documents=(privacy_document,),
                legal_context=platform_legal_context(),
            )
            if privacy_document is not None
            else ""
        )

    response = render(
        request,
        "businesses/signup_request_form.html",
        {
            "form": form,
            "privacy_document": privacy_document,
            "legal_presentation_token": legal_presentation_token,
            "legal_unavailable_message": legal_unavailable_message,
            "internal_login_image_url": get_platform_login_image_url(),
        },
        status=response_status,
    )
    response["Cache-Control"] = "no-store"
    # El formulario publica en el mismo origen. ``no-referrer`` puede hacer que
    # el navegador envíe ``Origin: null`` y Django lo rechace como CSRF.
    response["Referrer-Policy"] = "same-origin"
    return response


@require_GET
def business_signup_request_success(request):
    return render(
        request,
        "businesses/signup_request_success.html",
        {"internal_login_image_url": get_platform_login_image_url()},
    )

from django.shortcuts import redirect, render
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
from apps.legal.services import get_active_document


BUSINESS_SIGNUP_THROTTLE_MESSAGE = (
    "Ya hemos recibido varios envíos. Espera antes de volver a intentarlo."
)


@require_http_methods(["GET", "POST"])
def business_signup_request(request):
    form = BusinessSignupRequestForm(request.POST or None)
    response_status = 200

    if request.method == "POST":
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
            privacy_document = get_active_document(LegalDocument.Kind.PLATFORM_PRIVACY)
            if privacy_document is None:
                form.add_error(
                    None,
                    "Ahora mismo no podemos registrar la solicitud. Inténtalo de nuevo más tarde.",
                )
                response_status = 503
            else:
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
                    signup_request.privacy_acknowledged_at = timezone.now()
                    signup_request.save()
                return redirect("business_signup_request_success")

    if request.method == "POST" and form.errors:
        form.apply_error_accessibility()

    return render(
        request,
        "businesses/signup_request_form.html",
        {
            "form": form,
            "internal_login_image_url": get_platform_login_image_url(),
        },
        status=response_status,
    )


@require_GET
def business_signup_request_success(request):
    return render(
        request,
        "businesses/signup_request_success.html",
        {"internal_login_image_url": get_platform_login_image_url()},
    )

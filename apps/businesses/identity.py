from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.businesses.models import BusinessSignupRequest
from apps.core.phone import normalize_phone


@dataclass(frozen=True)
class ProfessionalIdentityConflicts:
    email: bool = False
    phone: bool = False

    @property
    def any(self):
        return self.email or self.phone


def professional_identity_conflicts(
    *,
    email="",
    phone="",
    exclude_signup_request_id=None,
    include_open_requests=True,
):
    """Comprueba la identidad interna sin mezclar las cuentas cliente."""

    email_normalized = (email or "").strip().lower()
    try:
        phone_normalized = normalize_phone(phone) if (phone or "").strip() else ""
    except ValidationError:
        phone_normalized = ""

    User = get_user_model()
    email_conflict = bool(
        email_normalized
        and User.objects.filter(email_normalized=email_normalized).exists()
    )
    phone_conflict = bool(
        phone_normalized
        and User.objects.filter(normalized_phone=phone_normalized).exists()
    )

    if include_open_requests and (email_normalized or phone_normalized):
        open_requests = BusinessSignupRequest.objects.filter(
            status__in=BusinessSignupRequest.open_statuses()
        )
        if exclude_signup_request_id is not None:
            open_requests = open_requests.exclude(pk=exclude_signup_request_id)
        if email_normalized:
            email_conflict = email_conflict or open_requests.filter(
                email_normalized=email_normalized
            ).exists()
        if phone_normalized:
            phone_conflict = phone_conflict or open_requests.filter(
                normalized_phone=phone_normalized
            ).exists()

    return ProfessionalIdentityConflicts(
        email=email_conflict,
        phone=phone_conflict,
    )

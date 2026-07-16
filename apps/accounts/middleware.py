from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse


class PasswordChangeRequiredMiddleware:
    """Keep temporary internal credentials outside the operational product."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        allowed_paths = {
            reverse("accounts:security"),
            reverse("accounts:logout"),
            reverse("accounts:logged_out"),
        }
        if (
            getattr(user, "is_authenticated", False)
            and user.password_change_required
            and request.path not in allowed_paths
        ):
            security_url = reverse("accounts:security")
            query = urlencode({"next": request.get_full_path()})
            return redirect(f"{security_url}?{query}")
        return self.get_response(request)


class EmailVerificationRequiredMiddleware:
    """Require a verified personal email before an internal account can operate."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        allowed_paths = {
            reverse("accounts:email"),
            reverse("accounts:security"),
            reverse("accounts:logout"),
            reverse("accounts:logged_out"),
        }
        if (
            getattr(user, "is_authenticated", False)
            and user.email_verification_required
            and request.path not in allowed_paths
            and not request.path.startswith("/cuenta/verificar-correo/")
        ):
            query = urlencode({"next": request.get_full_path()})
            return redirect(f'{reverse("accounts:email")}?{query}')
        return self.get_response(request)

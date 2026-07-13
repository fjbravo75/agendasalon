from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse

from apps.businesses.services import get_primary_business_for_user
from apps.legal.services import professional_legal_status


class ProfessionalLegalOnboardingMiddleware:
    """Impide trabajar con datos reales antes de cerrar el alta legal del negocio."""

    PROFESSIONAL_PREFIXES = ("/profesional/", "/clientes/profesional/")
    ALLOWED_PREFIXES = ("/profesional/privacidad/", "/cuenta/salir/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            getattr(user, "is_authenticated", False)
            and not user.is_superuser
            and request.path.startswith(self.PROFESSIONAL_PREFIXES)
            and not request.path.startswith(self.ALLOWED_PREFIXES)
        ):
            business = get_primary_business_for_user(user)
            if business is not None and not professional_legal_status(user, business)["is_current"]:
                onboarding_url = reverse("legal:professional_onboarding")
                query = urlencode({"next": request.get_full_path()})
                return redirect(f"{onboarding_url}?{query}")
        return self.get_response(request)

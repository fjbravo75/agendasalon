from apps.businesses.services import get_primary_business_for_user


def professional_appearance(request):
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False) or user.is_superuser:
        return {}
    if not (
        request.path.startswith("/profesional/")
        or request.path.startswith("/clientes/profesional/")
    ):
        return {}
    business = get_primary_business_for_user(user)
    if business is None:
        return {}
    return {
        "professional_business": business,
        "professional_theme": business.professional_theme,
    }

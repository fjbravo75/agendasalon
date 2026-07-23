from apps.businesses.services import get_platform_settings, get_primary_business_for_user


def professional_appearance(request):
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        return {}
    resolver_match = getattr(request, "resolver_match", None)
    legal_namespace = getattr(resolver_match, "namespace", None) == "legal"
    account_namespace = getattr(resolver_match, "namespace", None) == "accounts"
    if user.is_superuser:
        if not (
            request.path.startswith("/superadmin/")
            or legal_namespace
            or account_namespace
        ):
            return {}
        return {"professional_theme": get_platform_settings().admin_theme}
    if not (
        request.path.startswith("/profesional/")
        or request.path.startswith("/clientes/profesional/")
        or legal_namespace
        or account_namespace
    ):
        return {}
    business = get_primary_business_for_user(user)
    if business is None:
        return {}
    return {
        "professional_business": business,
        "professional_business_is_operational": business.is_operational_for_agenda(),
        "professional_theme": business.professional_theme,
    }

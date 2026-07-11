from django.conf import settings


class ContentSecurityPolicyMiddleware:
    """Añade CSP y políticas de navegador que no dependen del despliegue."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            policy = (
                settings.ADMIN_CONTENT_SECURITY_POLICY
                if request.path.startswith("/admin/")
                else settings.CONTENT_SECURITY_POLICY
            )
            response["Content-Security-Policy"] = policy
        response.setdefault("Permissions-Policy", settings.PERMISSIONS_POLICY)
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return response

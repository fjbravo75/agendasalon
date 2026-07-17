from django.shortcuts import redirect, render


def home(request):
    if request.user.is_authenticated:
        from apps.accounts.views import get_post_login_redirect_url

        return redirect(get_post_login_redirect_url(request.user))

    return redirect("accounts:login")


def csrf_failure(request, reason=""):
    """Respuesta pública estable sin exponer el motivo interno del rechazo."""

    response = render(request, "core/csrf_failure.html", status=403)
    # El rechazo puede producirse antes de entrar en una vista cuya URL contenga
    # un token. No se debe reenviar ni almacenar esa ruta sensible.
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response

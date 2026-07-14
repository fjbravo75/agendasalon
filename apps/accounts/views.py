from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.http import url_has_allowed_host_and_scheme, urlsafe_base64_decode
from django.views.decorators.http import require_POST

from apps.accounts.forms import (
    AccountEmailForm,
    AccountPasswordChangeForm,
    PhoneAuthenticationForm,
    ProfessionalActivationForm,
)
from apps.businesses.models import PlatformSettings
from apps.businesses.services import (
    get_platform_login_image_url,
    get_platform_settings,
    get_primary_business_for_user,
)
from apps.core.security_throttle import (
    THROTTLE_MESSAGE,
    ThrottleLimit,
    phone_throttle_key,
    request_ip,
    reserve_throttle_attempts,
    settle_successful_throttle,
)
from apps.notifications.services import (
    queue_and_dispatch,
    queue_professional_email_verification,
)


def get_post_login_redirect_url(user):
    if user.is_superuser:
        return reverse("dashboards:superadmin_home")
    if get_primary_business_for_user(user) is not None:
        return reverse("dashboards:professional_home")
    return reverse("accounts:no_business")


class AgendaSalonLoginView(LoginView):
    authentication_form = PhoneAuthenticationForm
    redirect_authenticated_user = True
    template_name = "accounts/login.html"

    def get_success_url(self):
        destination = self.get_redirect_url() or get_post_login_redirect_url(self.request.user)
        if self.request.user.password_change_required:
            query = urlencode({"next": destination})
            return f'{reverse("accounts:security")}?{query}'
        return destination

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        platform_settings = get_platform_settings()
        context["internal_login_image_url"] = get_platform_login_image_url(
            platform_settings
        )
        return context

    def _throttle_keys(self):
        subject = phone_throttle_key(self.request.POST.get("username", ""))
        return subject, request_ip(self.request)

    def post(self, request, *args, **kwargs):
        subject_key, ip_key = self._throttle_keys()
        self._throttle_reservation = reserve_throttle_attempts(
            limits=(
                ThrottleLimit("private_login_subject", subject_key, 5, 15 * 60),
                ThrottleLimit("private_login_ip", ip_key, 30, 15 * 60),
            )
        )
        if not self._throttle_reservation.allowed:
            form = self.get_form_class()(
                **self.get_form_kwargs(),
                skip_authentication=True,
            )
            form.is_valid()
            form.add_error(None, THROTTLE_MESSAGE)
            response = self.render_to_response(self.get_context_data(form=form))
            response.status_code = 429
            return response
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        reservation = self._throttle_reservation
        if reservation.blocked_scopes:
            form.add_error(None, THROTTLE_MESSAGE)
        response = super().form_invalid(form)
        if reservation.blocked_scopes:
            response.status_code = 429
        return response

    def form_valid(self, form):
        settle_successful_throttle(
            self._throttle_reservation,
            reset_scopes={"private_login_subject"},
        )
        return super().form_valid(form)


@login_required
def no_business(request):
    return render(request, "accounts/no_business.html")


def _safe_next_url(request):
    next_url = request.POST.get("next") or request.GET.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ""


@login_required
def account_security(request):
    forced = request.user.password_change_required
    form = AccountPasswordChangeForm(
        request.user,
        request.POST or None,
        forced=forced,
    )
    next_url = _safe_next_url(request)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.password_change_required = False
        user.save(update_fields=["password_change_required"])
        update_session_auth_hash(request, user)
        if forced:
            messages.success(
                request,
                "Tu contraseña personal ya está activa. Ya puedes continuar en AgendaSalon.",
            )
            return redirect(next_url or get_post_login_redirect_url(user))
        messages.success(request, "Tu contraseña se ha cambiado correctamente.")
        return redirect("accounts:security")
    return render(
        request,
        "accounts/security.html",
        {
            "password_form": form,
            "password_change_required": forced,
            "next_url": next_url,
        },
    )


def _user_from_token(uidb64, token):
    try:
        user = get_user_model().objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, get_user_model().DoesNotExist):
        return None
    if not default_token_generator.check_token(user, token):
        return None
    return user


def professional_activate(request, uidb64, token):
    user = _user_from_token(uidb64, token)
    valid = bool(user and not user.is_active and user.email_normalized)
    activation_form = ProfessionalActivationForm(user, request.POST or None) if valid else None
    if request.method == "POST" and activation_form is not None and activation_form.is_valid():
        user = activation_form.save(commit=False)
        user.is_active = True
        user.email_verified_at = timezone.now()
        user.email_verification_required = False
        user.password_change_required = False
        user.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Tu cuenta ya está activa. La contraseña solo la conoces tú.")
        return redirect(get_post_login_redirect_url(user))
    platform_settings = get_platform_settings()
    response = render(
        request,
        "accounts/activate.html",
        {
            "activation_form": activation_form,
            "activation_valid": valid,
            "activation_user": user,
            "internal_login_image_url": get_platform_login_image_url(platform_settings),
        },
    )
    response["Referrer-Policy"] = "same-origin"
    response["Cache-Control"] = "no-store"
    return response


@login_required
def account_email(request):
    next_url = _safe_next_url(request)
    if request.user.email_verified_at and not request.user.email_verification_required:
        return redirect(next_url or "accounts:security")
    form = AccountEmailForm(
        request.POST or None,
        user=request.user,
        initial={"email": request.user.email},
    )
    delivery = None
    if request.method == "POST" and form.is_valid():
        user = form.save()
        business = get_primary_business_for_user(user)
        delivery = queue_and_dispatch(
            queue_professional_email_verification(user, business=business)
        )
        if delivery.status == delivery.Status.SENT:
            messages.success(request, "Te hemos enviado el enlace de verificación.")
        else:
            messages.warning(
                request,
                "El correo ha quedado pendiente de envío. Puedes intentarlo de nuevo dentro de unos minutos.",
            )
    return render(
        request,
        "accounts/email.html",
        {"email_form": form, "next_url": next_url, "delivery": delivery},
    )


def professional_email_verify(request, uidb64, token):
    user = _user_from_token(uidb64, token)
    if user is None or not user.is_active or user.email_verified_at is not None:
        return render(request, "accounts/email_verified.html", {"verification_valid": False}, status=410)
    user.email_verified_at = timezone.now()
    user.email_verification_required = False
    user.save(update_fields=["email_verified_at", "email_verification_required"])
    if request.user.is_authenticated and request.user.pk == user.pk:
        messages.success(request, "Correo verificado. Ya puedes continuar en AgendaSalon.")
        return redirect(get_post_login_redirect_url(user))
    return render(request, "accounts/email_verified.html", {"verification_valid": True})
@login_required
@require_POST
def private_logout(request):
    if request.user.is_superuser:
        theme = get_platform_settings().admin_theme
    else:
        business = get_primary_business_for_user(request.user)
        theme = (
            business.professional_theme
            if business is not None
            else PlatformSettings.AdminTheme.LIGHT
        )
    logout(request)
    request.session["logged_out_theme"] = theme
    return redirect("accounts:logged_out")


def logged_out(request):
    if request.user.is_authenticated:
        return redirect(get_post_login_redirect_url(request.user))
    theme = request.session.get("logged_out_theme", PlatformSettings.AdminTheme.LIGHT)
    if theme not in PlatformSettings.AdminTheme.values:
        theme = PlatformSettings.AdminTheme.LIGHT
    return render(
        request,
        "accounts/logged_out.html",
        {"professional_theme": theme},
    )

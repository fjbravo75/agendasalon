from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts.forms import PhoneAuthenticationForm
from apps.businesses.models import PlatformSettings
from apps.businesses.services import (
    get_platform_login_image_url,
    get_platform_settings,
    get_primary_business_for_user,
)
from apps.core.security_throttle import (
    THROTTLE_MESSAGE,
    clear_failed_attempts,
    is_throttled,
    phone_throttle_key,
    record_failed_attempt,
    request_ip,
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
        return self.get_redirect_url() or get_post_login_redirect_url(self.request.user)

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
        if is_throttled(scope="private_login_subject", key=subject_key) or is_throttled(
            scope="private_login_ip", key=ip_key
        ):
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
        subject_key, ip_key = self._throttle_keys()
        subject_blocked = record_failed_attempt(
            scope="private_login_subject",
            key=subject_key,
            limit=5,
            window_seconds=15 * 60,
        )
        ip_blocked = record_failed_attempt(
            scope="private_login_ip",
            key=ip_key,
            limit=30,
            window_seconds=15 * 60,
        )
        if subject_blocked or ip_blocked:
            form.add_error(None, THROTTLE_MESSAGE)
        response = super().form_invalid(form)
        if subject_blocked or ip_blocked:
            response.status_code = 429
        return response

    def form_valid(self, form):
        subject_key, _ = self._throttle_keys()
        clear_failed_attempts(scope="private_login_subject", key=subject_key)
        return super().form_valid(form)


@login_required
def no_business(request):
    return render(request, "accounts/no_business.html")


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

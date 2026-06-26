from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.shortcuts import render
from django.urls import reverse

from apps.accounts.forms import PhoneAuthenticationForm
from apps.businesses.services import get_primary_business_for_user


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


@login_required
def no_business(request):
    return render(request, "accounts/no_business.html")

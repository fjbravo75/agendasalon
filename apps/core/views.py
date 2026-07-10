from django.shortcuts import redirect, render

from apps.businesses.models import Business


def home(request):
    if request.user.is_authenticated:
        from apps.accounts.views import get_post_login_redirect_url

        return redirect(get_post_login_redirect_url(request.user))

    public_businesses = Business.objects.filter(is_active=True).order_by("commercial_name", "pk")
    return render(request, "public/home.html", {"public_businesses": public_businesses})

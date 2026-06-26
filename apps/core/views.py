from django.shortcuts import redirect, render


def home(request):
    if request.user.is_authenticated:
        from apps.accounts.views import get_post_login_redirect_url

        return redirect(get_post_login_redirect_url(request.user))

    return render(request, "public/home.html")

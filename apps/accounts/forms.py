from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import AuthenticationForm

from apps.core.phone import normalize_phone


class PhoneAuthenticationForm(AuthenticationForm):
    """Authenticate internal SaaS users by normalized phone and password."""

    error_messages = {
        "invalid_login": "Teléfono o contraseña no válidos.",
        "inactive": "Esta cuenta está inactiva.",
    }

    username = forms.CharField(
        label="Teléfono",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "tel",
                "autofocus": True,
                "placeholder": "Teléfono (600 000 000)",
            }
        ),
    )
    password = forms.CharField(
        label="Contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "placeholder": "Tu contraseña",
            }
        ),
    )

    def __init__(self, *args, skip_authentication=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.skip_authentication = skip_authentication

    def clean(self):
        phone = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")

        if phone is not None and password:
            try:
                normalized_phone = normalize_phone(phone)
            except forms.ValidationError as exc:
                raise self.get_invalid_login_error() from exc

            self.cleaned_data["username"] = normalized_phone
            if self.skip_authentication:
                return self.cleaned_data
            self.user_cache = authenticate(
                self.request,
                username=normalized_phone,
                password=password,
            )

            if self.user_cache is None:
                raise self.get_invalid_login_error()

            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data

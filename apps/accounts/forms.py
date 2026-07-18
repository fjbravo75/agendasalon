from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, SetPasswordForm

from apps.core.email import normalize_and_validate_routable_email
from apps.core.features import transactional_email_delivery_enabled
from apps.core.phone import normalize_phone


DEMO_EMAIL_VALIDATION_MESSAGE = (
    "Usa una dirección de correo con formato y dominio válidos. En esta "
    "demostración académica no se entregan mensajes externos."
)


def _normalize_routable_email(value):
    try:
        return normalize_and_validate_routable_email(value)
    except forms.ValidationError as exc:
        if not transactional_email_delivery_enabled():
            raise forms.ValidationError(DEMO_EMAIL_VALIDATION_MESSAGE) from exc
        raise


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


class AccountPasswordChangeForm(PasswordChangeForm):
    """Change an authenticated internal account password without exposing it."""

    error_messages = {
        **PasswordChangeForm.error_messages,
        "password_incorrect": "La contraseña actual no es correcta.",
        "password_mismatch": "Las dos contraseñas nuevas no coinciden.",
    }

    def __init__(self, user, *args, forced=False, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.forced = forced
        self.fields["old_password"].label = (
            "Contraseña temporal" if forced else "Contraseña actual"
        )
        self.fields["new_password1"].label = "Nueva contraseña"
        self.fields["new_password2"].label = "Repite la nueva contraseña"
        self.fields["old_password"].widget.attrs.update(
            {
                "autocomplete": "current-password",
                "autofocus": True,
                "placeholder": "Escribe tu contraseña actual",
            }
        )
        for field_name in ("new_password1", "new_password2"):
            self.fields[field_name].widget.attrs.update(
                {
                    "autocomplete": "new-password",
                    "aria-describedby": "account-password-rules",
                }
            )
        self.fields["new_password1"].widget.attrs["placeholder"] = (
            "Mínimo 12 caracteres"
        )
        self.fields["new_password2"].widget.attrs["placeholder"] = (
            "Escríbela de nuevo"
        )
        for field in self.fields.values():
            field.help_text = ""

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("new_password2")
        if password and self.user.check_password(password):
            self.add_error(
                "new_password2",
                "La nueva contraseña debe ser diferente de la actual.",
            )
        return cleaned_data


class ProfessionalActivationForm(SetPasswordForm):
    """Let a new professional choose a private password from a one-time link."""

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.fields["new_password1"].label = "Crea tu contraseña"
        self.fields["new_password2"].label = "Repite la contraseña"
        self.fields["new_password1"].widget.attrs.update(
            {"autocomplete": "new-password", "placeholder": "Mínimo 12 caracteres"}
        )
        self.fields["new_password2"].widget.attrs.update(
            {"autocomplete": "new-password", "placeholder": "Escríbela de nuevo"}
        )
        for field in self.fields.values():
            field.help_text = ""


class AccountEmailForm(forms.Form):
    email = forms.EmailField(
        label="Correo electrónico",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "autofocus": True,
                "placeholder": "tu@negocio.es",
            }
        ),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_email(self):
        email = _normalize_routable_email(self.cleaned_data["email"])
        if get_user_model().objects.exclude(pk=self.user.pk).filter(
            email_normalized=email
        ).exists():
            raise forms.ValidationError("Este correo ya pertenece a otra cuenta.")
        return email

    def save(self):
        self.user.email = self.cleaned_data["email"]
        self.user.email_verified_at = None
        self.user.email_verification_required = True
        self.user.save(
            update_fields=[
                "email",
                "email_normalized",
                "email_verified_at",
                "email_verification_required",
            ]
        )
        return self.user

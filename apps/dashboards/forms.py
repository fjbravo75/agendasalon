import hmac

from django import forms


DEMO_REFRESH_CONFIRMATION_PHRASE = "REGENERAR DEMO"


class DemoRefreshConfirmationForm(forms.Form):
    current_password = forms.CharField(
        label="Contraseña actual",
        max_length=128,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "aria-describedby": "demo-refresh-password-help",
            }
        ),
    )
    confirmation_phrase = forms.CharField(
        label="Frase de confirmación",
        max_length=32,
        strip=False,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "aria-describedby": "demo-refresh-phrase-help",
            }
        ),
    )
    destructive_scope_confirmed = forms.BooleanField(
        label=(
            "Entiendo que se eliminarán los cambios y datos mutables actuales y que "
            "la demostración volverá a su escenario canónico."
        ),
        required=True,
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def full_clean(self):
        super().full_clean()
        for field_name in self.errors:
            if field_name in self.fields:
                self.fields[field_name].widget.attrs["aria-invalid"] = "true"

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.user.is_active or not self.user.check_password(password):
            raise forms.ValidationError("La contraseña actual no es correcta.")
        return password

    def clean_confirmation_phrase(self):
        phrase = self.cleaned_data["confirmation_phrase"]
        if not hmac.compare_digest(phrase, DEMO_REFRESH_CONFIRMATION_PHRASE):
            raise forms.ValidationError(
                f"Escribe exactamente {DEMO_REFRESH_CONFIRMATION_PHRASE}."
            )
        return phrase

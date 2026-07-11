from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.core.phone import normalize_phone
from apps.customers.services import (
    authenticate_client_access,
    create_or_reuse_professional_client,
    register_client_access,
    save_authorized_contact,
    update_professional_client,
)
from apps.customers.models import BusinessClientAuthorizedContact


class ClientLoginForm(forms.Form):
    phone = forms.CharField(
        label="Teléfono",
        max_length=32,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "Teléfono (600 000 000)"}),
    )
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password", "placeholder": "Tu contraseña"}),
    )

    def __init__(self, *args, business, skip_authentication=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.skip_authentication = skip_authentication
        self.client_access = None

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        try:
            normalize_phone(phone)
        except DjangoValidationError as exc:
            raise forms.ValidationError("Revisa el teléfono.") from exc
        return phone

    def clean(self):
        cleaned_data = super().clean()
        phone = cleaned_data.get("phone")
        password = cleaned_data.get("password")
        if not phone or not password:
            return cleaned_data
        if self.skip_authentication:
            return cleaned_data

        self.client_access = authenticate_client_access(
            business=self.business,
            phone=phone,
            password=password,
        )
        if self.client_access is None:
            raise forms.ValidationError("Teléfono o contraseña no válidos.")
        return cleaned_data


class ClientRegistrationForm(forms.Form):
    full_name = forms.CharField(
        label="Nombre",
        max_length=160,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Nombre completo"}),
    )
    phone = forms.CharField(
        label="Teléfono",
        max_length=32,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "Teléfono (600 000 000)"}),
    )
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Crea tu contraseña",
                "aria-describedby": "password-requirements",
            }
        ),
    )
    password_confirm = forms.CharField(
        label="Repite la contraseña",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "placeholder": "Repite tu contraseña"}),
    )

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.client_access = None

    def clean_full_name(self):
        full_name = self.cleaned_data["full_name"].strip()
        if not full_name:
            raise forms.ValidationError("Indica tu nombre.")
        return full_name

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        try:
            normalize_phone(phone)
        except DjangoValidationError as exc:
            raise forms.ValidationError("Revisa el teléfono.") from exc
        return phone

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")
        if password and password_confirm and password != password_confirm:
            self.add_error("password_confirm", "Las contraseñas no coinciden.")
        if password:
            try:
                validate_password(password)
            except DjangoValidationError as exc:
                self.add_error("password", exc)
        return cleaned_data

    def save(self):
        try:
            self.client_access = register_client_access(
                business=self.business,
                full_name=self.cleaned_data["full_name"],
                phone=self.cleaned_data["phone"],
                password=self.cleaned_data["password"],
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(getattr(exc, "messages", [str(exc)])) from exc
        return self.client_access


class ClientInvitationActivationForm(forms.Form):
    password = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Crea tu contraseña",
                "aria-describedby": "password-requirements",
            }
        ),
    )
    password_confirm = forms.CharField(
        label="Repite la contraseña",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Repite tu contraseña",
            }
        ),
    )

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get("password")
        password_confirm = cleaned_data.get("password_confirm")
        if password and password_confirm and password != password_confirm:
            self.add_error("password_confirm", "Las contraseñas no coinciden.")
        if password:
            try:
                validate_password(password)
            except DjangoValidationError as exc:
                self.add_error("password", exc)
        return cleaned_data


class ProfessionalClientQuickForm(forms.Form):
    full_name = forms.CharField(
        label="Nombre completo",
        max_length=160,
        widget=forms.TextInput(
            attrs={"autocomplete": "name", "placeholder": "Nombre completo"}
        ),
    )
    phone = forms.CharField(
        label="Teléfono",
        max_length=32,
        widget=forms.TelInput(
            attrs={
                "autocomplete": "tel",
                "inputmode": "tel",
                "placeholder": "Teléfono (600 000 000)",
            }
        ),
    )
    email = forms.EmailField(
        label="Correo electrónico (opcional)",
        required=False,
        widget=forms.EmailInput(
            attrs={"autocomplete": "email", "placeholder": "Email opcional"}
        ),
    )
    internal_notes = forms.CharField(
        label="Notas internas (opcional)",
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Nota breve para el equipo, si hace falta",
            }
        ),
    )

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.client = None
        self.created = False

    def clean_full_name(self):
        full_name = self.cleaned_data["full_name"].strip()
        if not full_name:
            raise forms.ValidationError("Indica el nombre del cliente.")
        return full_name

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        try:
            normalize_phone(phone)
        except DjangoValidationError as exc:
            raise forms.ValidationError("Revisa el teléfono.") from exc
        return phone

    def clean_internal_notes(self):
        return (self.cleaned_data.get("internal_notes") or "").strip()

    def save(self):
        try:
            self.client, self.created = create_or_reuse_professional_client(
                business=self.business,
                full_name=self.cleaned_data["full_name"],
                phone=self.cleaned_data["phone"],
                email=self.cleaned_data.get("email") or "",
                internal_notes=self.cleaned_data.get("internal_notes") or "",
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(getattr(exc, "messages", [str(exc)])) from exc
        return self.client, self.created


class ProfessionalClientEditForm(forms.Form):
    full_name = forms.CharField(
        label="Nombre completo",
        max_length=160,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Nombre completo"}),
    )
    phone = forms.CharField(
        label="Teléfono",
        max_length=32,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "Teléfono (600 000 000)"}),
    )
    email = forms.EmailField(
        label="Email",
        required=False,
        widget=forms.EmailInput(attrs={"autocomplete": "email", "placeholder": "Email opcional"}),
    )
    internal_notes = forms.CharField(
        label="Notas internas",
        required=False,
        max_length=800,
        widget=forms.Textarea(
            attrs={"rows": 5, "placeholder": "Información útil para atender a esta persona"}
        ),
    )

    def __init__(self, *args, business, instance, **kwargs):
        if (not args or args[0] is None) and "initial" not in kwargs:
            kwargs["initial"] = {
                "full_name": instance.full_name,
                "phone": instance.phone,
                "email": instance.email,
                "internal_notes": instance.internal_notes,
            }
        super().__init__(*args, **kwargs)
        self.business = business
        self.instance = instance

    def clean_full_name(self):
        full_name = self.cleaned_data["full_name"].strip()
        if not full_name:
            raise forms.ValidationError("Indica el nombre del cliente.")
        return full_name

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        try:
            normalize_phone(phone)
        except DjangoValidationError as exc:
            raise forms.ValidationError("Revisa el teléfono.") from exc
        return phone

    def clean_internal_notes(self):
        return (self.cleaned_data.get("internal_notes") or "").strip()

    def save(self):
        return update_professional_client(
            client=self.instance,
            full_name=self.cleaned_data["full_name"],
            phone=self.cleaned_data["phone"],
            email=self.cleaned_data.get("email") or "",
            internal_notes=self.cleaned_data.get("internal_notes") or "",
        )


class ProfessionalAuthorizedContactForm(forms.Form):
    full_name = forms.CharField(
        label="Nombre completo",
        max_length=160,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Nombre completo"}),
    )
    phone = forms.CharField(
        label="Teléfono",
        max_length=32,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "Teléfono (600 000 000)"}),
    )
    relationship_label = forms.ChoiceField(
        label="Relación con el cliente",
        choices=[("", "Selecciona la relación")]
        + list(BusinessClientAuthorizedContact.Relationship.choices),
    )
    is_primary_contact = forms.BooleanField(
        label="Contacto principal",
        required=False,
    )
    notes = forms.CharField(
        label="Notas",
        required=False,
        max_length=500,
        widget=forms.Textarea(
            attrs={"rows": 4, "placeholder": "Qué conviene saber cuando esta persona pida una cita"}
        ),
    )

    def __init__(self, *args, business, business_client, instance=None, **kwargs):
        if (not args or args[0] is None) and instance is not None and "initial" not in kwargs:
            kwargs["initial"] = {
                "full_name": instance.full_name,
                "phone": instance.phone,
                "relationship_label": instance.relationship_label,
                "is_primary_contact": instance.is_primary_contact,
                "notes": instance.notes,
            }
        super().__init__(*args, **kwargs)
        self.business = business
        self.business_client = business_client
        self.instance = instance

    def clean_full_name(self):
        full_name = self.cleaned_data["full_name"].strip()
        if not full_name:
            raise forms.ValidationError("Indica el nombre de la persona autorizada.")
        return full_name

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        try:
            normalize_phone(phone)
        except DjangoValidationError as exc:
            raise forms.ValidationError("Revisa el teléfono.") from exc
        return phone

    def clean_notes(self):
        return (self.cleaned_data.get("notes") or "").strip()

    def save(self):
        return save_authorized_contact(
            business=self.business,
            business_client=self.business_client,
            contact=self.instance,
            full_name=self.cleaned_data["full_name"],
            phone=self.cleaned_data["phone"],
            relationship_label=self.cleaned_data["relationship_label"],
            is_primary_contact=self.cleaned_data.get("is_primary_contact", False),
            notes=self.cleaned_data.get("notes") or "",
        )

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from apps.core.phone import normalize_phone
from apps.core.text import normalize_search_text
from apps.customers.services import (
    authenticate_client_access,
    create_or_reuse_professional_client,
    register_client_access,
    save_authorized_contact,
    update_professional_client,
)
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccessGrant,
    BusinessClientAuthorizedContact,
)


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
        label="Teléfono propio (opcional)",
        required=False,
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
    authorized_business_client = forms.ModelChoiceField(
        queryset=BusinessClient.objects.none(),
        required=False,
        widget=forms.HiddenInput(attrs={"data-client-search-id": ""}),
    )
    authorized_client_search = forms.CharField(
        label="Buscar persona autorizada (opcional)",
        required=False,
        max_length=160,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Nombre o teléfono de un cliente registrado",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "new-client-authorized-results",
                "data-client-search-input": "",
            }
        ),
    )
    authorized_relationship = forms.ChoiceField(
        label="Relación con el nuevo cliente",
        required=False,
        choices=[("", "Selecciona la relación")]
        + list(BusinessClientAuthorizedContact.Relationship.choices),
    )
    authorized_allow_online = forms.BooleanField(
        label="También puede reservar online",
        required=False,
        widget=forms.CheckboxInput(attrs={"data-online-toggle": ""}),
    )

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.client = None
        self.created = False
        self.authorized_contact = None
        self.fields["authorized_business_client"].queryset = BusinessClient.objects.filter(
            business=business,
            is_active=True,
        )

    def clean_full_name(self):
        full_name = self.cleaned_data["full_name"].strip()
        if not full_name:
            raise forms.ValidationError("Indica el nombre del cliente.")
        return full_name

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            return ""
        try:
            normalize_phone(phone)
        except DjangoValidationError as exc:
            raise forms.ValidationError("Revisa el teléfono.") from exc
        return phone

    def clean_internal_notes(self):
        return (self.cleaned_data.get("internal_notes") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        authorized_client = cleaned_data.get("authorized_business_client")
        if authorized_client is None:
            cleaned_data["authorized_relationship"] = ""
            cleaned_data["authorized_allow_online"] = False
            return cleaned_data

        if not authorized_client.phone_normalized:
            self.add_error(
                "authorized_client_search",
                "La persona autorizada necesita un teléfono propio.",
            )
        if (
            normalize_search_text(cleaned_data.get("full_name") or "")
            == authorized_client.full_name_normalized
            and cleaned_data.get("phone")
            and normalize_phone(cleaned_data["phone"]) == authorized_client.phone_normalized
        ):
            self.add_error(
                "authorized_client_search",
                "El titular y la persona autorizada deben ser personas distintas.",
            )
        if not cleaned_data.get("authorized_relationship"):
            self.add_error(
                "authorized_relationship",
                "Indica la relación con el nuevo cliente.",
            )
        if cleaned_data.get("authorized_allow_online"):
            access = getattr(authorized_client, "access", None)
            if access is None or not access.is_active:
                self.add_error(
                    "authorized_allow_online",
                    "Esta persona todavía no tiene una cuenta online activa.",
                )
        return cleaned_data

    def save(self):
        try:
            with transaction.atomic():
                self.client, self.created = create_or_reuse_professional_client(
                    business=self.business,
                    full_name=self.cleaned_data["full_name"],
                    phone=self.cleaned_data["phone"],
                    email=self.cleaned_data.get("email") or "",
                    internal_notes=self.cleaned_data.get("internal_notes") or "",
                )
                authorized_client = self.cleaned_data.get("authorized_business_client")
                if authorized_client is not None:
                    if authorized_client.pk == self.client.pk:
                        raise DjangoValidationError(
                            "El titular y la persona autorizada deben ser personas distintas."
                        )
                    self.authorized_contact = save_authorized_contact(
                        business=self.business,
                        business_client=self.client,
                        linked_business_client=authorized_client,
                        full_name=authorized_client.full_name,
                        phone=authorized_client.phone,
                        relationship_label=self.cleaned_data["authorized_relationship"],
                        is_primary_contact=True,
                        notes="Puede pedir citas en nombre del titular de esta ficha.",
                        allow_online_booking=self.cleaned_data.get(
                            "authorized_allow_online", False
                        ),
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
        label="Teléfono propio (opcional)",
        required=False,
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
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            return ""
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
    class ContactType:
        REGISTERED = "registered"
        EXTERNAL = "external"

    contact_type = forms.ChoiceField(
        label="Tipo de persona",
        choices=(
            (ContactType.REGISTERED, "Sí, ya es cliente"),
            (ContactType.EXTERNAL, "No, es un contacto externo"),
        ),
        initial=ContactType.REGISTERED,
        required=False,
        widget=forms.RadioSelect,
    )
    linked_business_client = forms.ModelChoiceField(
        queryset=BusinessClient.objects.none(),
        required=False,
        widget=forms.HiddenInput(attrs={"data-client-search-id": ""}),
    )
    client_search = forms.CharField(
        label="Buscar cliente registrado",
        required=False,
        max_length=160,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Empieza a escribir un nombre o teléfono",
                "role": "combobox",
                "aria-autocomplete": "list",
                "aria-expanded": "false",
                "aria-controls": "authorized-client-results",
                "data-client-search-input": "",
            }
        ),
    )
    full_name = forms.CharField(
        label="Nombre completo",
        required=False,
        max_length=160,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Nombre completo"}),
    )
    phone = forms.CharField(
        label="Teléfono",
        required=False,
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
    allow_online_booking = forms.BooleanField(
        label="También puede reservar online",
        required=False,
        widget=forms.CheckboxInput(attrs={"data-online-toggle": ""}),
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
            linked_client = instance.linked_business_client
            kwargs["initial"] = {
                "contact_type": (
                    self.ContactType.REGISTERED if linked_client else self.ContactType.EXTERNAL
                ),
                "linked_business_client": linked_client,
                "client_search": linked_client.full_name if linked_client else "",
                "full_name": instance.full_name,
                "phone": instance.phone,
                "relationship_label": instance.relationship_label,
                "is_primary_contact": instance.is_primary_contact,
                "notes": instance.notes,
                "allow_online_booking": BusinessClientAccessGrant.objects.filter(
                    authorized_contact=instance,
                    is_active=True,
                ).exists(),
            }
        super().__init__(*args, **kwargs)
        self.business = business
        self.business_client = business_client
        self.instance = instance
        self.fields["linked_business_client"].queryset = BusinessClient.objects.filter(
            business=business,
            is_active=True,
        ).exclude(pk=business_client.pk)

    def clean_full_name(self):
        return (self.cleaned_data.get("full_name") or "").strip()

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            return ""
        try:
            normalize_phone(phone)
        except DjangoValidationError as exc:
            raise forms.ValidationError("Revisa el teléfono.") from exc
        return phone

    def clean_notes(self):
        return (self.cleaned_data.get("notes") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        contact_type = cleaned_data.get("contact_type") or (
            self.ContactType.REGISTERED
            if cleaned_data.get("linked_business_client")
            else self.ContactType.EXTERNAL
        )
        cleaned_data["contact_type"] = contact_type
        linked_client = cleaned_data.get("linked_business_client")

        if contact_type == self.ContactType.REGISTERED:
            if linked_client is None:
                self.add_error("client_search", "Selecciona una persona de la lista.")
                return cleaned_data
            if not linked_client.phone_normalized:
                self.add_error(
                    "client_search",
                    "La ficha seleccionada necesita un teléfono propio para actuar como persona autorizada.",
                )
            cleaned_data["full_name"] = linked_client.full_name
            cleaned_data["phone"] = linked_client.phone

            duplicate = BusinessClientAuthorizedContact.objects.filter(
                business_client=self.business_client,
                linked_business_client=linked_client,
            )
            if self.instance is not None:
                duplicate = duplicate.exclude(pk=self.instance.pk)
            if duplicate.exists():
                self.add_error("client_search", "Esta persona ya está vinculada a la ficha.")

            if cleaned_data.get("allow_online_booking"):
                access = getattr(linked_client, "access", None)
                if access is None or not access.is_active:
                    self.add_error(
                        "allow_online_booking",
                        "Esta persona todavía no tiene una cuenta online activa.",
                    )
        else:
            cleaned_data["linked_business_client"] = None
            if not cleaned_data.get("full_name"):
                self.add_error("full_name", "Indica el nombre de la persona autorizada.")
            if not cleaned_data.get("phone"):
                self.add_error("phone", "Indica su teléfono.")
            cleaned_data["allow_online_booking"] = False
        return cleaned_data

    def save(self):
        return save_authorized_contact(
            business=self.business,
            business_client=self.business_client,
            contact=self.instance,
            linked_business_client=self.cleaned_data.get("linked_business_client"),
            full_name=self.cleaned_data["full_name"],
            phone=self.cleaned_data["phone"],
            relationship_label=self.cleaned_data["relationship_label"],
            is_primary_contact=self.cleaned_data.get("is_primary_contact", False),
            notes=self.cleaned_data.get("notes") or "",
            allow_online_booking=self.cleaned_data.get("allow_online_booking", False),
        )

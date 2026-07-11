from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from apps.businesses.images import (
    PUBLIC_IMAGE_MAX_INPUT_BYTES,
    PUBLIC_IMAGE_MAX_INPUT_PIXELS,
    PublicImageProcessingError,
    sanitize_public_image,
)
from apps.businesses.models import Business, BusinessMembership
from apps.core.phone import normalize_phone


class BusinessForm(forms.ModelForm):
    slug = forms.SlugField(
        label="Identificador público",
        max_length=180,
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Se genera desde el nombre si lo dejas vacío",
                "title": "Se usa en la dirección pública del negocio.",
            }
        ),
    )

    class Meta:
        model = Business
        fields = (
            "commercial_name",
            "slug",
            "public_phone",
            "public_email",
            "address",
            "city",
            "province",
            "public_description",
            "is_active",
            "public_booking_enabled",
        )
        labels = {
            "commercial_name": "Nombre comercial",
            "slug": "Identificador público",
            "public_phone": "Teléfono público",
            "public_email": "Correo público",
            "address": "Dirección",
            "city": "Localidad",
            "province": "Provincia",
            "public_description": "Descripción pública",
            "is_active": "Negocio activo",
            "public_booking_enabled": "Reserva pública activa",
        }
        widgets = {
            "commercial_name": forms.TextInput(
                attrs={"autocomplete": "organization", "placeholder": "Ej. Peluquería Mari"}
            ),
            "public_phone": forms.TelInput(
                attrs={"autocomplete": "tel", "placeholder": "Ej. 600 111 001"}
            ),
            "public_email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "Ej. hola@negocio.es"}
            ),
            "address": forms.TextInput(
                attrs={"autocomplete": "street-address", "placeholder": "Ej. Calle Mayor 12"}
            ),
            "city": forms.TextInput(
                attrs={"autocomplete": "address-level2", "placeholder": "Ej. Madrid"}
            ),
            "province": forms.TextInput(
                attrs={"autocomplete": "address-level1", "placeholder": "Ej. Madrid"}
            ),
            "public_description": forms.Textarea(
                attrs={
                    "rows": 5,
                    "placeholder": "Describe brevemente el negocio para sus clientes.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and not self.is_bound:
            self.initial.setdefault("is_active", True)
            self.initial.setdefault("public_booking_enabled", False)

    def clean_slug(self):
        slug = slugify(self.cleaned_data.get("slug") or self.cleaned_data.get("commercial_name") or "")
        if not slug:
            raise ValidationError("Indica un nombre comercial válido.")
        duplicates = Business.objects.filter(slug=slug)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError("Ya existe un negocio con este identificador público.")
        return slug

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("public_booking_enabled") and not cleaned_data.get("is_active"):
            self.add_error(
                "public_booking_enabled",
                "Activa primero el negocio para poder abrir su reserva pública.",
            )
        return cleaned_data


class ProfessionalCreateForm(forms.Form):
    full_name = forms.CharField(
        label="Nombre del profesional",
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "name",
                "placeholder": "Ej. Laura García",
            }
        ),
    )
    phone = forms.CharField(
        label="Teléfono de acceso",
        max_length=32,
        widget=forms.TelInput(
            attrs={
                "autocomplete": "tel",
                "inputmode": "tel",
                "placeholder": "Ej. 600 111 001",
            }
        ),
        help_text="Será su identificador para entrar en AgendaSalon.",
    )
    email = forms.EmailField(
        label="Correo electrónico (opcional)",
        required=False,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "placeholder": "Ej. profesional@negocio.es",
            }
        ),
    )
    password = forms.CharField(
        label="Contraseña temporal",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Mínimo 8 caracteres",
            }
        ),
        help_text="Debe tener al menos 8 caracteres y no ser demasiado común.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("phone", "password"):
            self.fields[field_name].widget.attrs["aria-describedby"] = (
                "professional-access-guidance"
            )

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        try:
            normalized_phone = normalize_phone(phone)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        if get_user_model().objects.filter(normalized_phone=normalized_phone).exists():
            raise forms.ValidationError("Ya existe una cuenta interna con este teléfono.")
        self.normalized_phone = normalized_phone
        return phone

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def create_professional(self, *, business):
        user = get_user_model().objects.create_user(
            normalized_phone=self.normalized_phone,
            phone=self.cleaned_data["phone"],
            password=self.cleaned_data["password"],
            full_name=self.cleaned_data["full_name"],
            email=self.cleaned_data["email"],
        )
        BusinessMembership.objects.create(
            business=business,
            user=user,
            role=BusinessMembership.Role.PROFESSIONAL_ADMIN,
            is_active=True,
        )
        return user


class BusinessVisualSettingsForm(forms.ModelForm):
    remove_public_image = forms.BooleanField(
        label="Volver a la imagen predeterminada",
        required=False,
    )

    class Meta:
        model = Business
        fields = ("professional_theme", "public_image")
        labels = {
            "professional_theme": "Apariencia del panel",
            "public_image": "Nueva imagen pública",
        }
        error_messages = {
            "public_image": {
                "invalid_image": "Selecciona una imagen JPG, PNG o WebP válida.",
            }
        }
        widgets = {
            "professional_theme": forms.RadioSelect,
            "public_image": forms.FileInput(
                attrs={
                    "accept": "image/jpeg,image/png,image/webp",
                }
            ),
        }

    def clean_public_image(self):
        image = self.cleaned_data.get("public_image")
        if not image or "public_image" not in self.files:
            return image
        if image.size > PUBLIC_IMAGE_MAX_INPUT_BYTES:
            raise forms.ValidationError("La imagen no puede superar los 5 MB.")
        verified_image = getattr(image, "image", None)
        image_format = getattr(verified_image, "format", "")
        if image_format not in {"JPEG", "PNG", "WEBP"}:
            raise forms.ValidationError("Usa una imagen JPG, PNG o WebP.")
        width = getattr(verified_image, "width", 0)
        height = getattr(verified_image, "height", 0)
        if width * height > PUBLIC_IMAGE_MAX_INPUT_PIXELS:
            raise forms.ValidationError("La imagen tiene demasiados píxeles para un uso seguro.")
        if width < 800 or height < 500:
            raise forms.ValidationError("La imagen debe medir al menos 800 × 500 píxeles.")
        try:
            return sanitize_public_image(image)
        except PublicImageProcessingError as exc:
            raise forms.ValidationError(
                "No hemos podido preparar la imagen. Prueba con otro archivo JPG, PNG o WebP."
            ) from exc

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("remove_public_image") and self.files.get("public_image"):
            self.add_error(
                "public_image",
                "Elige una imagen nueva o vuelve a la predeterminada, pero no ambas opciones.",
            )
        return cleaned_data

    def save(self, commit=True):
        business = super().save(commit=False)
        if self.cleaned_data.get("remove_public_image"):
            business.public_image = None
        if commit:
            business.save()
        return business

from pathlib import Path

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.templatetags.static import static
from django.utils.text import slugify

from apps.businesses.images import (
    PUBLIC_IMAGE_MAX_INPUT_BYTES,
    PUBLIC_IMAGE_MAX_INPUT_PIXELS,
    PUBLIC_IMAGE_MAX_RETAINED_PER_BUSINESS,
    PublicImageProcessingError,
    sanitize_public_image,
)
from apps.businesses.models import (
    Business,
    BusinessMembership,
    BusinessPublicImage,
    BusinessSignupRequest,
    PlatformLoginImage,
    PlatformSettings,
)
from apps.core.email import normalize_and_validate_routable_email
from apps.core.features import transactional_email_delivery_enabled
from apps.core.phone import normalize_phone


DEMO_EMAIL_VALIDATION_MESSAGE = (
    "Usa una dirección de correo con formato y dominio válidos. El envío de "
    "correos está desactivado en este entorno."
)


def _normalize_routable_email(value):
    try:
        return normalize_and_validate_routable_email(value)
    except ValidationError as exc:
        if not transactional_email_delivery_enabled():
            raise forms.ValidationError(DEMO_EMAIL_VALIDATION_MESSAGE) from exc
        raise


def _sanitize_visual_image(image):
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
        help_texts = {
            "public_email": (
                "Puede mostrarse a los clientes. No se utiliza para avisos internos."
            )
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

    def clean_public_email(self):
        email = self.cleaned_data.get("public_email")
        if not email:
            return ""
        try:
            return _normalize_routable_email(email)
        except ValidationError:
            existing_email = (self.instance.public_email or "").strip().lower()
            if self.instance.pk and email.strip().lower() == existing_email:
                return existing_email
            raise

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
        label="Correo de acceso",
        max_length=254,
        required=True,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "placeholder": "Ej. profesional@negocio.es",
            }
        ),
        help_text=(
            "Enviaremos aquí un enlace para activar la cuenta y crear su contraseña. "
            "Si es el primer acceso del negocio, también se propondrá como correo de "
            "avisos; después podrá cambiarse en Ajustes."
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not transactional_email_delivery_enabled():
            self.fields["email"].help_text = (
                "Se guardará como dato del acceso y se propondrá como correo de avisos. "
                "El envío de correos está desactivado en este entorno."
            )
        for field_name in ("phone", "email"):
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

    def clean_email(self):
        email = _normalize_routable_email(self.cleaned_data["email"])
        if get_user_model().objects.filter(email_normalized=email).exists():
            raise forms.ValidationError("Ya existe una cuenta interna con este correo.")
        return email

    def create_professional(self, *, business):
        user = get_user_model().objects.create_user(
            normalized_phone=self.normalized_phone,
            phone=self.cleaned_data["phone"],
            password=None,
            full_name=self.cleaned_data["full_name"],
            email=self.cleaned_data["email"],
            is_active=False,
            password_change_required=False,
            email_verification_required=True,
        )
        BusinessMembership.objects.create(
            business=business,
            user=user,
            role=BusinessMembership.Role.PROFESSIONAL_ADMIN,
            is_active=True,
        )
        return user


class BusinessSignupRequestForm(forms.ModelForm):
    business_type = forms.ChoiceField(
        label="Tipo de negocio",
        choices=(("", "Selecciona un tipo"), *BusinessSignupRequest.BusinessType.choices),
    )
    preferred_channel = forms.ChoiceField(
        label="¿Cómo prefieres que contactemos contigo?",
        choices=BusinessSignupRequest.PreferredChannel.choices,
        widget=forms.RadioSelect(),
    )
    privacy_acknowledged = forms.BooleanField(
        label="He leído la información sobre el tratamiento de mis datos.",
        required=True,
    )
    email = forms.EmailField(
        label="Correo electrónico",
        max_length=254,
        required=True,
        widget=forms.EmailInput(
            attrs={"autocomplete": "email", "placeholder": "Ej. nombre@correo.es"}
        ),
        help_text="Lo usaremos para responderte y activar el acceso si aprobamos el alta.",
        error_messages={
            "required": "Indica un correo para recibir la respuesta y activar el acceso."
        },
    )

    class Meta:
        model = BusinessSignupRequest
        fields = (
            "business_name",
            "business_type",
            "city",
            "province",
            "contact_name",
            "phone",
            "email",
            "preferred_channel",
            "need_text",
        )
        labels = {
            "business_name": "Nombre comercial",
            "business_type": "Tipo de negocio",
            "city": "Localidad",
            "province": "Provincia (opcional)",
            "contact_name": "Tu nombre",
            "phone": "Teléfono",
            "email": "Correo electrónico",
            "preferred_channel": "¿Cómo prefieres que contactemos contigo?",
            "need_text": "¿Qué te gustaría organizar mejor? (opcional)",
        }
        widgets = {
            "business_name": forms.TextInput(
                attrs={"autocomplete": "organization", "placeholder": "Ej. Peluquería Mari"}
            ),
            "city": forms.TextInput(
                attrs={"autocomplete": "address-level2", "placeholder": "Ej. Córdoba"}
            ),
            "province": forms.TextInput(
                attrs={"autocomplete": "address-level1", "placeholder": "Ej. Córdoba"}
            ),
            "contact_name": forms.TextInput(
                attrs={"autocomplete": "name", "placeholder": "Nombre y apellidos"}
            ),
            "phone": forms.TelInput(
                attrs={"autocomplete": "tel", "inputmode": "tel", "placeholder": "Ej. 600 111 001"}
            ),
            "email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "Ej. nombre@correo.es"}
            ),
            "need_text": forms.Textarea(
                attrs={
                    "rows": 4,
                    "maxlength": 300,
                    "placeholder": "Cuéntanos brevemente qué parte de tu agenda te da más trabajo.",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not transactional_email_delivery_enabled():
            self.fields["email"].help_text = (
                "Lo guardaremos como dato de contacto. El envío de correos está "
                "desactivado en este entorno."
            )
            self.fields["email"].error_messages["required"] = (
                "Indica un correo válido como dato de contacto. El envío de correos "
                "está desactivado en este entorno."
            )

    def clean_phone(self):
        phone = self.cleaned_data["phone"]
        try:
            self.normalized_phone = normalize_phone(phone)
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages) from exc
        return phone

    def clean_email(self):
        email = _normalize_routable_email(self.cleaned_data.get("email", ""))
        if not email:
            if not transactional_email_delivery_enabled():
                raise forms.ValidationError(
                    "Indica un correo válido como dato de contacto. El envío de "
                    "correos está desactivado en este entorno."
                )
            raise forms.ValidationError(
                "Indica un correo para recibir la respuesta y activar el acceso."
            )
        return email

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get("preferred_channel")
            == BusinessSignupRequest.PreferredChannel.EMAIL
            and not cleaned_data.get("email")
            and "email" not in self.errors
        ):
            self.add_error("email", "Indica un correo para poder contactar por este canal.")
        return cleaned_data

    def apply_error_accessibility(self):
        for field_name in self.errors:
            if field_name not in self.fields:
                continue
            field = self.fields[field_name]
            error_id = f"{self[field_name].id_for_label}-error"
            described_by = field.widget.attrs.get("aria-describedby", "").split()
            if error_id not in described_by:
                described_by.append(error_id)
            field.widget.attrs["aria-describedby"] = " ".join(described_by)
            field.widget.attrs["aria-invalid"] = "true"


class BusinessSignupRequestReviewForm(forms.ModelForm):
    status = forms.ChoiceField(
        label="Estado de la solicitud",
        choices=(
            (BusinessSignupRequest.Status.NEW, "Nueva"),
            (BusinessSignupRequest.Status.REVIEWING, "En revisión"),
            (BusinessSignupRequest.Status.CONTACTED, "Contactada"),
            (BusinessSignupRequest.Status.DISMISSED, "Descartada"),
        ),
    )

    class Meta:
        model = BusinessSignupRequest
        fields = ("status", "admin_note")
        labels = {"admin_note": "Nota interna"}
        widgets = {
            "admin_note": forms.Textarea(
                attrs={"rows": 5, "maxlength": 1000, "placeholder": "Seguimiento, acuerdos o motivo de descarte."}
            )
        }

    def clean_admin_note(self):
        note = self.cleaned_data["admin_note"].strip()
        if len(note) > 1000:
            raise forms.ValidationError("La nota no puede superar los 1000 caracteres.")
        return note


class BusinessVisualSettingsForm(forms.ModelForm):
    public_image_choice = forms.ChoiceField(
        label="Imagen activa",
        required=False,
        error_messages={"invalid_choice": "Selecciona una imagen disponible."},
    )
    new_public_image = forms.ImageField(
        label="Subir una imagen nueva",
        required=False,
        error_messages={
            "invalid_image": "Selecciona una imagen JPG, PNG o WebP válida.",
        },
        widget=forms.FileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "class": "visually-hidden settings-file-input",
                "data-public-image-upload": "",
            }
        ),
    )

    class Meta:
        model = Business
        fields = ("professional_theme", "public_image_choice", "new_public_image")
        labels = {
            "professional_theme": "Apariencia del panel",
            "public_image_choice": "Imagen activa",
            "new_public_image": "Subir una imagen nueva",
        }
        widgets = {
            "professional_theme": forms.RadioSelect,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.uploaded_image_label = "Imagen personalizada"
        self.saved_public_image = None
        custom_images = tuple(self.instance.public_images.all())
        self.public_image_quota_reached = (
            len(custom_images) >= PUBLIC_IMAGE_MAX_RETAINED_PER_BUSINESS
        )
        if self.public_image_quota_reached:
            self.fields["new_public_image"].widget.attrs.update(
                {"disabled": True, "aria-disabled": "true"}
            )
        choices = [
            ("preset:salon", "Salón luminoso"),
            ("preset:barberia", "Barbería contemporánea"),
            *[(f"custom:{image.pk}", image.label) for image in custom_images],
        ]
        self.fields["public_image_choice"].choices = choices

        selected_custom = next((image for image in custom_images if image.is_selected), None)
        current_choice = (
            f"custom:{selected_custom.pk}"
            if selected_custom is not None
            else f"preset:{self.instance.public_image_preset}"
        )
        self.initial["public_image_choice"] = current_choice
        selected_value = str(self["public_image_choice"].value() or current_choice)

        self.public_image_options = [
            {
                "value": "preset:salon",
                "label": "Salón luminoso",
                "description": "Ambiente claro y cálido para peluquería, belleza o estética.",
                "url": static("img/customer-login-peluqueria-mari-bg.webp"),
                "theme": "salon",
                "is_selected": selected_value == "preset:salon",
            },
            {
                "value": "preset:barberia",
                "label": "Barbería contemporánea",
                "description": "Ambiente oscuro y sobrio para barbería o cuidado masculino.",
                "url": static("img/customer-login-barberia-norte-bg-v2.webp"),
                "theme": "barberia",
                "is_selected": selected_value == "preset:barberia",
            },
        ]
        for image in custom_images:
            try:
                image_url = image.image.url
            except ValueError:
                continue
            self.public_image_options.append(
                {
                    "value": f"custom:{image.pk}",
                    "label": image.label,
                    "description": "Imagen subida por este negocio.",
                    "url": image_url,
                    "theme": "custom",
                    "is_selected": selected_value == f"custom:{image.pk}",
                }
            )

        self.active_public_image_label = next(
            (
                option["label"]
                for option in self.public_image_options
                if option["is_selected"]
            ),
            "Salón luminoso",
        )

    def clean_new_public_image(self):
        image = self.cleaned_data.get("new_public_image")
        if not image or "new_public_image" not in self.files:
            return image
        if self.instance.public_images.count() >= PUBLIC_IMAGE_MAX_RETAINED_PER_BUSINESS:
            raise forms.ValidationError(
                "Este negocio ya tiene 12 imágenes guardadas. "
                "Elige una de ellas para continuar."
            )
        self.uploaded_image_label = Path(image.name or "Imagen personalizada").stem[:120]
        return _sanitize_visual_image(image)

    def clean_public_image_choice(self):
        choice = self.cleaned_data.get("public_image_choice")
        if not choice:
            selected = self.instance.public_images.filter(is_selected=True).first()
            return (
                f"custom:{selected.pk}"
                if selected is not None
                else f"preset:{self.instance.public_image_preset}"
            )
        if choice in {"preset:salon", "preset:barberia"}:
            return choice
        if choice.startswith("custom:"):
            try:
                image_id = int(choice.split(":", 1)[1])
            except (TypeError, ValueError):
                raise forms.ValidationError("Selecciona una imagen disponible.") from None
            if self.instance.public_images.filter(pk=image_id).exists():
                return choice
        raise forms.ValidationError("Selecciona una imagen disponible.")

    def save(self, commit=True, uploaded_by=None):
        business = super().save(commit=False)
        if commit:
            with transaction.atomic():
                business.save(update_fields=["professional_theme", "updated_at"])
                locked_business = Business.objects.select_for_update().get(pk=business.pk)
                uploaded_image = self.cleaned_data.get("new_public_image")
                choice = self.cleaned_data["public_image_choice"]
                if uploaded_image:
                    if (
                        locked_business.public_images.count()
                        >= PUBLIC_IMAGE_MAX_RETAINED_PER_BUSINESS
                    ):
                        raise ValidationError(
                            {
                                "new_public_image": (
                                    "Este negocio ya tiene 12 imágenes guardadas. "
                                    "Elige una de ellas para continuar."
                                )
                            }
                        )
                    locked_business.public_images.filter(is_selected=True).update(
                        is_selected=False
                    )
                    self.saved_public_image = BusinessPublicImage.objects.create(
                        business=locked_business,
                        image=uploaded_image,
                        label=self.uploaded_image_label,
                        is_selected=True,
                        uploaded_by=uploaded_by,
                    )
                elif choice.startswith("custom:"):
                    image_id = int(choice.split(":", 1)[1])
                    locked_business.public_images.filter(is_selected=True).exclude(
                        pk=image_id
                    ).update(is_selected=False)
                    locked_business.public_images.filter(pk=image_id).update(is_selected=True)
                else:
                    locked_business.public_images.filter(is_selected=True).update(
                        is_selected=False
                    )
                    business.public_image_preset = choice.split(":", 1)[1]
                    business.save(update_fields=["public_image_preset", "updated_at"])
        return business


class PlatformVisualSettingsForm(forms.ModelForm):
    login_image_choice = forms.ChoiceField(
        label="Imagen activa",
        required=False,
        error_messages={"invalid_choice": "Selecciona una imagen disponible."},
    )
    new_login_image = forms.ImageField(
        label="Subir una imagen nueva",
        required=False,
        error_messages={
            "invalid_image": "Selecciona una imagen JPG, PNG o WebP válida.",
        },
        widget=forms.FileInput(
            attrs={
                "accept": "image/jpeg,image/png,image/webp",
                "class": "visually-hidden settings-file-input",
                "data-public-image-upload": "",
            }
        ),
    )

    class Meta:
        model = PlatformSettings
        fields = ("admin_theme", "login_image_choice", "new_login_image")
        labels = {
            "admin_theme": "Apariencia de la administración",
            "login_image_choice": "Imagen activa",
            "new_login_image": "Subir una imagen nueva",
        }
        widgets = {"admin_theme": forms.RadioSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.uploaded_image_label = "Imagen personalizada"
        self.saved_login_image = None
        custom_images = tuple(self.instance.login_images.all())
        choices = [
            ("preset:agendasalon", "AgendaSalon"),
            ("preset:salon", "Salón luminoso"),
            ("preset:barberia", "Barbería contemporánea"),
            *[(f"custom:{image.pk}", image.label) for image in custom_images],
        ]
        self.fields["login_image_choice"].choices = choices

        selected_custom = next((image for image in custom_images if image.is_selected), None)
        current_choice = (
            f"custom:{selected_custom.pk}"
            if selected_custom is not None
            else f"preset:{self.instance.login_image_preset}"
        )
        self.initial["login_image_choice"] = current_choice
        selected_value = str(self["login_image_choice"].value() or current_choice)

        self.login_image_options = [
            {
                "value": "preset:agendasalon",
                "label": "AgendaSalon",
                "description": "Imagen editorial propia del acceso interno de la plataforma.",
                "url": static("img/agendasalon-internal-login-bg.webp"),
                "theme": "agendasalon",
                "is_selected": selected_value == "preset:agendasalon",
            },
            {
                "value": "preset:salon",
                "label": "Salón luminoso",
                "description": "Ambiente claro y cálido vinculado al sector de belleza.",
                "url": static("img/customer-login-peluqueria-mari-bg.webp"),
                "theme": "salon",
                "is_selected": selected_value == "preset:salon",
            },
            {
                "value": "preset:barberia",
                "label": "Barbería contemporánea",
                "description": "Ambiente oscuro y sobrio de barbería y cuidado masculino.",
                "url": static("img/customer-login-barberia-norte-bg-v2.webp"),
                "theme": "barberia",
                "is_selected": selected_value == "preset:barberia",
            },
        ]
        for image in custom_images:
            try:
                image_url = image.image.url
            except ValueError:
                continue
            self.login_image_options.append(
                {
                    "value": f"custom:{image.pk}",
                    "label": image.label,
                    "description": "Imagen subida por la administración de AgendaSalon.",
                    "url": image_url,
                    "theme": "custom",
                    "is_selected": selected_value == f"custom:{image.pk}",
                }
            )

    def clean_new_login_image(self):
        image = self.cleaned_data.get("new_login_image")
        if not image or "new_login_image" not in self.files:
            return image
        self.uploaded_image_label = Path(image.name or "Imagen personalizada").stem[:120]
        return _sanitize_visual_image(image)

    def clean_login_image_choice(self):
        choice = self.cleaned_data.get("login_image_choice")
        if not choice:
            selected = self.instance.login_images.filter(is_selected=True).first()
            return (
                f"custom:{selected.pk}"
                if selected is not None
                else f"preset:{self.instance.login_image_preset}"
            )
        allowed_presets = {"preset:agendasalon", "preset:salon", "preset:barberia"}
        if choice in allowed_presets:
            return choice
        if choice.startswith("custom:"):
            try:
                image_id = int(choice.split(":", 1)[1])
            except (TypeError, ValueError):
                raise forms.ValidationError("Selecciona una imagen disponible.") from None
            if self.instance.login_images.filter(pk=image_id).exists():
                return choice
        raise forms.ValidationError("Selecciona una imagen disponible.")

    def save(self, commit=True, updated_by=None):
        platform_settings = super().save(commit=False)
        platform_settings.pk = PlatformSettings.SINGLETON_PK
        platform_settings.updated_by = updated_by
        if commit:
            platform_settings.save(
                update_fields=["admin_theme", "updated_by", "updated_at"]
            )
            uploaded_image = self.cleaned_data.get("new_login_image")
            choice = self.cleaned_data["login_image_choice"]
            if uploaded_image:
                platform_settings.login_images.filter(is_selected=True).update(
                    is_selected=False
                )
                self.saved_login_image = PlatformLoginImage.objects.create(
                    platform_settings=platform_settings,
                    image=uploaded_image,
                    label=self.uploaded_image_label,
                    is_selected=True,
                    uploaded_by=updated_by,
                )
            elif choice.startswith("custom:"):
                image_id = int(choice.split(":", 1)[1])
                platform_settings.login_images.filter(is_selected=True).exclude(
                    pk=image_id
                ).update(is_selected=False)
                platform_settings.login_images.filter(pk=image_id).update(is_selected=True)
            else:
                platform_settings.login_images.filter(is_selected=True).update(
                    is_selected=False
                )
                platform_settings.login_image_preset = choice.split(":", 1)[1]
                platform_settings.save(
                    update_fields=["login_image_preset", "updated_by", "updated_at"]
                )
        return platform_settings

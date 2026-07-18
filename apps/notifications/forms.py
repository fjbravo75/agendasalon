from uuid import uuid4

from django import forms

from apps.businesses.models import Business, PlatformSettings
from apps.core.email import normalize_and_validate_routable_email


class _OperationalEmailForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        email_field = self.fields["notification_email"]
        email_field.widget.attrs["aria-describedby"] = "notification-email-help"
        if self.is_bound and "notification_email" in self.errors:
            email_field.widget.attrs["aria-invalid"] = "true"

    def clean_notification_email(self):
        value = (self.cleaned_data.get("notification_email") or "").strip()
        if not value:
            return ""
        return normalize_and_validate_routable_email(value)

    def _apply_email_state(self, instance, *, verified_at=None):
        normalized = self.cleaned_data["notification_email"]
        changed = normalized != (instance.notification_email_normalized or "")
        instance.notification_email = normalized
        instance.notification_email_normalized = normalized
        if changed:
            instance.notification_email_verification_nonce = uuid4()
            instance.notification_email_verified_at = verified_at
        elif verified_at is not None and instance.notification_email_verified_at is None:
            instance.notification_email_verified_at = verified_at
        if not normalized:
            instance.notification_email_verified_at = None
            instance.notifications_enabled = False
        return instance


class PlatformNotificationSettingsForm(_OperationalEmailForm):
    class Meta:
        model = PlatformSettings
        fields = (
            "notification_email",
            "notifications_enabled",
            "notify_continuity",
            "notify_demo_refresh",
            "notify_signup_requests",
            "notify_email_failures",
        )
        labels = {
            "notification_email": "Correo de avisos",
            "notifications_enabled": "Recibir avisos por correo",
            "notify_continuity": "Continuidad y copias",
            "notify_demo_refresh": "Regeneración de la demostración",
            "notify_signup_requests": "Altas de negocio",
            "notify_email_failures": "Fallos definitivos de correo",
        }
        widgets = {
            "notification_email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "avisos@ejemplo.es"}
            )
        }

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        instance = super().save(commit=False)
        verified_at = None
        normalized = self.cleaned_data["notification_email"]
        if (
            normalized
            and self.actor
            and self.actor.email_verified_at
            and self.actor.email_normalized == normalized
        ):
            verified_at = self.actor.email_verified_at
        self._apply_email_state(instance, verified_at=verified_at)
        if commit:
            instance.updated_by = self.actor
            instance.save(
                update_fields=[
                    "notification_email",
                    "notification_email_normalized",
                    "notification_email_verified_at",
                    "notification_email_verification_nonce",
                    "notifications_enabled",
                    "notify_continuity",
                    "notify_demo_refresh",
                    "notify_signup_requests",
                    "notify_email_failures",
                    "updated_by",
                    "updated_at",
                ]
            )
        return instance


class BusinessNotificationSettingsForm(_OperationalEmailForm):
    class Meta:
        model = Business
        fields = (
            "notification_email",
            "notifications_enabled",
            "notify_new_appointments",
            "notify_cancellations",
            "notify_client_access",
            "notify_holiday_reviews",
            "notify_email_failures",
        )
        labels = {
            "notification_email": "Correo de avisos",
            "notifications_enabled": "Recibir avisos por correo",
            "notify_new_appointments": "Nuevas citas",
            "notify_cancellations": "Cancelaciones",
            "notify_client_access": "Altas y accesos de clientes",
            "notify_holiday_reviews": "Citas por revisar en festivos",
            "notify_email_failures": "Fallos definitivos de correo",
        }
        widgets = {
            "notification_email": forms.EmailInput(
                attrs={"autocomplete": "email", "placeholder": "avisos@negocio.es"}
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["form"] = "business-notifications-form"

    def save(self, commit=True):
        instance = super().save(commit=False)
        normalized = self.cleaned_data["notification_email"]
        verified_at = None
        if normalized:
            verified_at = (
                instance.memberships.filter(
                    is_active=True,
                    user__is_active=True,
                    user__email_normalized=normalized,
                    user__email_verified_at__isnull=False,
                )
                .values_list("user__email_verified_at", flat=True)
                .first()
            )
        self._apply_email_state(instance, verified_at=verified_at)
        if commit:
            instance.save(
                update_fields=[
                    "notification_email",
                    "notification_email_normalized",
                    "notification_email_verified_at",
                    "notification_email_verification_nonce",
                    "notifications_enabled",
                    "notify_new_appointments",
                    "notify_cancellations",
                    "notify_client_access",
                    "notify_holiday_reviews",
                    "notify_email_failures",
                    "updated_at",
                ]
            )
        return instance

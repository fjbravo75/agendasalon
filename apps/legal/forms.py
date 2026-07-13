from django import forms

from apps.legal.models import CustomerPrivacyEvidence, DataRightsRequest


class BusinessLegalOnboardingForm(forms.Form):
    legal_name = forms.CharField(
        label="Nombre o razón social",
        max_length=180,
        widget=forms.TextInput(attrs={"autocomplete": "organization"}),
    )
    tax_identifier = forms.CharField(
        label="NIF o identificación fiscal",
        max_length=40,
        widget=forms.TextInput(attrs={"autocomplete": "off"}),
    )
    registered_address = forms.CharField(
        label="Domicilio del responsable",
        max_length=255,
        widget=forms.TextInput(attrs={"autocomplete": "street-address"}),
    )
    privacy_email = forms.EmailField(
        label="Correo para privacidad",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    rights_contact_name = forms.CharField(
        label="Persona o área de contacto (opcional)",
        max_length=160,
        required=False,
    )
    retention_criteria = forms.CharField(
        label="Criterio de conservación",
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Describe el criterio sin inventar un plazo que no puedas sostener.",
    )
    platform_privacy_acknowledged = forms.BooleanField(
        label="He leído la información sobre el tratamiento de mis datos profesionales.",
    )
    terms_accepted = forms.BooleanField(
        label="He leído y acepto las condiciones del servicio de AgendaSalon.",
    )
    data_processing_accepted = forms.BooleanField(
        label="Acepto el acuerdo de encargo de tratamiento en nombre del negocio.",
    )
    authority_declared = forms.BooleanField(
        label="Declaro que tengo autorización para actuar en nombre del negocio.",
    )

    def profile_data(self):
        return {
            field: self.cleaned_data[field]
            for field in (
                "legal_name",
                "tax_identifier",
                "registered_address",
                "privacy_email",
                "rights_contact_name",
                "retention_criteria",
            )
        }


class CustomerPrivacyEvidenceForm(forms.Form):
    channel = forms.ChoiceField(
        label="Canal utilizado",
        choices=(
            (CustomerPrivacyEvidence.Channel.IN_PERSON, "En el establecimiento"),
            (CustomerPrivacyEvidence.Channel.PHONE, "Por teléfono"),
            (CustomerPrivacyEvidence.Channel.WHATSAPP, "Por WhatsApp"),
            (CustomerPrivacyEvidence.Channel.EMAIL, "Por correo electrónico"),
            (CustomerPrivacyEvidence.Channel.OTHER, "Otro canal"),
        ),
    )


class DataRightsRequestForm(forms.ModelForm):
    class Meta:
        model = DataRightsRequest
        fields = ("request_type", "detail")
        labels = {
            "request_type": "Derecho que quieres ejercer",
            "detail": "Información adicional (opcional)",
        }
        widgets = {
            "detail": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Indica solo la información necesaria para tramitar tu solicitud.",
                }
            )
        }


class DataRightsResolutionForm(forms.ModelForm):
    class Meta:
        model = DataRightsRequest
        fields = ("status", "resolution_note")
        labels = {
            "status": "Estado",
            "resolution_note": "Nota de gestión",
        }
        widgets = {"resolution_note": forms.Textarea(attrs={"rows": 3})}

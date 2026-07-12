from django import forms


class NationalHolidaySyncForm(forms.Form):
    year = forms.IntegerField(
        label="Año",
        min_value=2000,
        max_value=2100,
        widget=forms.NumberInput(
            attrs={
                "inputmode": "numeric",
                "min": "2000",
                "max": "2100",
            }
        ),
        help_text="Importa los festivos comunes a todo el territorio publicados por el BOE.",
    )

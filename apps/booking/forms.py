import re

from django import forms
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.booking.models import Appointment, AvailabilityRule, BusinessClosure, Service, WorkLine
from apps.customers.models import BusinessClient


SERVICE_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SERVICE_COLOR_PALETTE = (
    {"hex": "#08927F", "name": "Verde salón"},
    {"hex": "#2F6F73", "name": "Verde petróleo"},
    {"hex": "#2F6F5E", "name": "Verde bosque"},
    {"hex": "#5BBFAD", "name": "Menta"},
    {"hex": "#28A6A6", "name": "Turquesa"},
    {"hex": "#3A9FBF", "name": "Cian"},
    {"hex": "#2C7FB8", "name": "Azul océano"},
    {"hex": "#5B9BD5", "name": "Azul cielo"},
    {"hex": "#5079BD", "name": "Azul sereno"},
    {"hex": "#355070", "name": "Azul marino"},
    {"hex": "#8274C9", "name": "Lavanda"},
    {"hex": "#8C5AA6", "name": "Violeta"},
    {"hex": "#7A4E73", "name": "Ciruela"},
    {"hex": "#D87093", "name": "Rosa"},
    {"hex": "#C98C9A", "name": "Rosa empolvado"},
    {"hex": "#D96F5D", "name": "Coral"},
    {"hex": "#B85C4A", "name": "Terracota"},
    {"hex": "#B94A5A", "name": "Cereza"},
    {"hex": "#7F3348", "name": "Granate"},
    {"hex": "#E58A3A", "name": "Naranja"},
    {"hex": "#E9A76F", "name": "Melocotón"},
    {"hex": "#E5A63A", "name": "Mostaza"},
    {"hex": "#E0C34B", "name": "Amarillo"},
    {"hex": "#8A9A4A", "name": "Oliva"},
    {"hex": "#99B857", "name": "Lima suave"},
    {"hex": "#708090", "name": "Gris azulado"},
    {"hex": "#9A8C84", "name": "Gris piedra"},
    {"hex": "#C2A878", "name": "Arena"},
    {"hex": "#8F6B4A", "name": "Chocolate"},
    {"hex": "#4B4F52", "name": "Carbón"},
)
WEEKDAY_CHOICES = (
    (0, "Lunes"),
    (1, "Martes"),
    (2, "Miércoles"),
    (3, "Jueves"),
    (4, "Viernes"),
    (5, "Sábado"),
    (6, "Domingo"),
)
PROFESSIONAL_CHANNEL_CHOICES = (
    (Appointment.ManualChannel.PHONE, "Teléfono"),
    (Appointment.ManualChannel.WHATSAPP, "WhatsApp"),
    (Appointment.ManualChannel.EMAIL, "Correo electrónico"),
    (Appointment.ManualChannel.FRONT_DESK, "Mostrador"),
    (Appointment.ManualChannel.OTHER, "Otro"),
)
CLOSURE_TYPE_CHOICES = (
    (BusinessClosure.ClosureType.VACATION, "Vacaciones"),
    (BusinessClosure.ClosureType.LOCAL_HOLIDAY, "Festivo local"),
    (BusinessClosure.ClosureType.REGIONAL_MANUAL_HOLIDAY, "Festivo autonómico manual"),
    (BusinessClosure.ClosureType.PUNCTUAL_BLOCK, "Bloqueo puntual"),
    (BusinessClosure.ClosureType.BUSINESS_CLOSURE, "Cierre de negocio"),
    (BusinessClosure.ClosureType.OTHER, "Otro"),
)


class AppointmentSearchForm(forms.Form):
    business_client = forms.ModelChoiceField(
        label="Cliente",
        queryset=BusinessClient.objects.none(),
        empty_label="Selecciona un cliente",
        error_messages={"required": "Selecciona un cliente."},
    )
    manual_channel = forms.ChoiceField(
        label="Canal",
        choices=PROFESSIONAL_CHANNEL_CHOICES,
        initial=Appointment.ManualChannel.PHONE,
        error_messages={"required": "Selecciona el canal."},
    )
    requested_by_contact = forms.ChoiceField(
        label="¿Quién pide la cita?",
        required=False,
        choices=(("self", "El propio cliente"),),
    )
    services = forms.ModelMultipleChoiceField(
        label="Servicios",
        queryset=Service.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "Selecciona al menos un servicio."},
    )
    target_date = forms.DateField(
        label="Día",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages={"required": "Indica el día de la cita."},
    )
    adjusted_duration_minutes = forms.IntegerField(
        label="Ajustar duración",
        required=False,
        min_value=15,
    )
    duration_adjustment_reason = forms.CharField(
        label="Motivo del ajuste",
        required=False,
        max_length=255,
    )

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.fields["business_client"].queryset = BusinessClient.objects.filter(
            business=business,
            is_active=True,
        ).order_by("full_name", "pk")
        self.fields["services"].queryset = Service.objects.filter(
            business=business,
            is_active=True,
        ).order_by("display_order", "name", "pk")
        self.fields["business_client"].label_from_instance = (
            lambda client: client.full_name
        )
        self.fields["services"].label_from_instance = (
            lambda service: f"{service.name} - {service.duration_minutes} min"
        )

        selected_client_id = self.data.get("business_client") or self.initial.get("business_client")
        try:
            selected_client = self.fields["business_client"].queryset.get(pk=selected_client_id)
        except (BusinessClient.DoesNotExist, TypeError, ValueError):
            selected_client = None
        if selected_client is not None:
            self.fields["requested_by_contact"].choices = [
                ("self", f"{selected_client.full_name} (para sí)"),
                *[
                    (f"contact:{contact.id}", f"{contact.full_name} · {contact.get_relationship_label_display()}")
                    for contact in selected_client.authorized_contacts.filter(is_active=True).order_by(
                        "-is_primary_contact", "full_name", "pk"
                    )
                ],
            ]

        self.fields["adjusted_duration_minutes"].widget.attrs.update(
            {
                "step": "15",
                "placeholder": "Opcional",
            }
        )

    def clean_services(self):
        services = self.cleaned_data["services"]
        if not services:
            raise forms.ValidationError("Selecciona al menos un servicio.")
        return services

    def clean_adjusted_duration_minutes(self):
        duration = self.cleaned_data.get("adjusted_duration_minutes")
        if duration is None:
            return duration
        if duration % 15 != 0:
            raise forms.ValidationError("La duración debe usar tramos de 15 minutos.")
        return duration

    def clean(self):
        cleaned_data = super().clean()
        services = cleaned_data.get("services")
        adjusted_duration = cleaned_data.get("adjusted_duration_minutes")
        reason = (cleaned_data.get("duration_adjustment_reason") or "").strip()

        if services:
            calculated_duration = sum(service.duration_minutes for service in services)
            cleaned_data["calculated_duration_minutes"] = calculated_duration
            final_duration = adjusted_duration or calculated_duration
            cleaned_data["final_duration_minutes"] = final_duration

            if adjusted_duration and adjusted_duration != calculated_duration and not reason:
                self.add_error(
                    "duration_adjustment_reason",
                    "Indica el motivo si ajustas la duración calculada.",
                )
        return cleaned_data

    def clean_requested_by_contact(self):
        value = self.cleaned_data.get("requested_by_contact") or "self"
        client = self.cleaned_data.get("business_client")
        if value == "self":
            return None
        if not value.startswith("contact:") or client is None:
            raise forms.ValidationError("Elige quién ha pedido la cita.")
        try:
            contact_id = int(value.split(":", 1)[1])
        except (TypeError, ValueError):
            raise forms.ValidationError("Elige quién ha pedido la cita.") from None
        contact = client.authorized_contacts.filter(pk=contact_id, is_active=True).first()
        if contact is None:
            raise forms.ValidationError("Esa persona ya no está autorizada para pedir citas.")
        return contact


class AppointmentCancelForm(forms.Form):
    cancellation_reason = forms.CharField(
        label="Motivo de cancelación",
        max_length=255,
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Ej. Cliente avisa que no puede venir",
            }
        ),
    )

    def clean_cancellation_reason(self):
        reason = (self.cleaned_data.get("cancellation_reason") or "").strip()
        if not reason:
            raise forms.ValidationError("Indica el motivo de cancelación.")
        return reason


class ServiceForm(forms.ModelForm):
    class Meta:
        model = Service
        fields = (
            "name",
            "duration_minutes",
            "price_amount",
            "color_hex",
            "display_order",
            "description",
            "is_active",
        )
        labels = {
            "name": "Nombre",
            "duration_minutes": "Duración",
            "price_amount": "Precio",
            "color_hex": "Color",
            "display_order": "Orden",
            "description": "Notas del servicio",
            "is_active": "Disponible para reservar",
        }
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": "Ej. Corte y peinado",
                }
            ),
            "duration_minutes": forms.NumberInput(
                attrs={
                    "min": "15",
                    "step": "15",
                    "placeholder": "Duración en minutos",
                }
            ),
            "price_amount": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "0.50",
                    "placeholder": "Precio opcional",
                }
            ),
            "color_hex": forms.HiddenInput(),
            "display_order": forms.NumberInput(
                attrs={
                    "min": "0",
                    "step": "1",
                    "placeholder": "Orden",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Detalle breve para el equipo, si hace falta",
                }
            ),
        }

    def __init__(self, *args, business, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        self.color_palette = SERVICE_COLOR_PALETTE
        self.fields["is_active"].required = False
        if self.instance.pk is None:
            self.fields["name"].widget.attrs["autofocus"] = "autofocus"
        if not self.is_bound and not self.initial.get("color_hex"):
            self.initial["color_hex"] = SERVICE_COLOR_PALETTE[0]["hex"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Indica el nombre del servicio.")

        duplicate_services = Service.objects.filter(
            business=self.business,
            name__iexact=name,
        )
        if self.instance.pk:
            duplicate_services = duplicate_services.exclude(pk=self.instance.pk)
        if duplicate_services.exists():
            raise forms.ValidationError("Ya existe un servicio con ese nombre.")
        return name

    def clean_description(self):
        return (self.cleaned_data.get("description") or "").strip()

    def clean_duration_minutes(self):
        duration = self.cleaned_data["duration_minutes"]
        if duration <= 0:
            raise forms.ValidationError("La duración debe ser positiva.")
        if duration % 15 != 0:
            raise forms.ValidationError("Usa tramos de 15 minutos.")
        return duration

    def clean_price_amount(self):
        price = self.cleaned_data.get("price_amount")
        if price is not None and price < 0:
            raise forms.ValidationError("El precio no puede ser negativo.")
        return price

    def clean_color_hex(self):
        color = (self.cleaned_data.get("color_hex") or "").strip().upper()
        allowed_colors = {option["hex"] for option in SERVICE_COLOR_PALETTE}
        if not SERVICE_COLOR_RE.match(color) or color not in allowed_colors:
            raise forms.ValidationError("Selecciona un color de la paleta.")
        return color

    def save(self, commit=True):
        service = super().save(commit=False)
        service.business = self.business
        if commit:
            service.full_clean()
            service.save()
        return service


class AvailabilityRuleForm(forms.ModelForm):
    class Meta:
        model = AvailabilityRule
        fields = ("weekday", "start_time", "end_time", "is_active")
        labels = {
            "weekday": "Día",
            "start_time": "Desde",
            "end_time": "Hasta",
            "is_active": "Usar este tramo",
        }
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }

    def __init__(self, *args, business, **kwargs):
        self.business = business
        super().__init__(*args, **kwargs)
        self.fields["weekday"].choices = WEEKDAY_CHOICES
        self.fields["is_active"].required = False
        self.fields["start_time"].widget.attrs.update({"placeholder": "09:00"})
        self.fields["end_time"].widget.attrs.update({"placeholder": "14:00"})

    def clean(self):
        cleaned_data = super().clean()
        weekday = cleaned_data.get("weekday")
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        is_active = cleaned_data.get("is_active")

        if start_time and end_time and start_time >= end_time:
            self.add_error("end_time", "La hora final debe ser posterior al inicio.")

        if weekday is not None and start_time and end_time and is_active:
            overlaps = AvailabilityRule.objects.filter(
                business=self.business,
                weekday=weekday,
                is_active=True,
                start_time__lt=end_time,
                end_time__gt=start_time,
            )
            if self.instance.pk:
                overlaps = overlaps.exclude(pk=self.instance.pk)
            if overlaps.exists():
                self.add_error(None, "Ese tramo se solapa con otro horario activo del mismo día.")
        return cleaned_data

    def save(self, commit=True):
        rule = super().save(commit=False)
        rule.business = self.business
        if commit:
            rule.full_clean()
            rule.save()
        return rule


class WorkLineForm(forms.ModelForm):
    class Meta:
        model = WorkLine
        fields = ("line_number", "name", "display_order", "is_active")
        labels = {
            "line_number": "Número",
            "name": "Nombre visible",
            "display_order": "Orden",
            "is_active": "Línea disponible",
        }
        widgets = {
            "line_number": forms.NumberInput(attrs={"min": "1", "max": "3", "step": "1"}),
            "name": forms.TextInput(attrs={"autocomplete": "off", "placeholder": "Ej. Línea 1 o Silla 1"}),
            "display_order": forms.NumberInput(attrs={"min": "0", "step": "1"}),
        }

    def __init__(self, *args, business, **kwargs):
        self.business = business
        self._was_active = bool(kwargs.get("instance") and kwargs["instance"].is_active)
        super().__init__(*args, **kwargs)
        self.fields["is_active"].required = False

    def clean_line_number(self):
        line_number = self.cleaned_data["line_number"]
        if not 1 <= line_number <= 3:
            raise forms.ValidationError("La línea debe estar entre 1 y 3.")
        duplicates = WorkLine.objects.filter(business=self.business, line_number=line_number)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise forms.ValidationError("Ya existe una línea con ese número.")
        return line_number

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def clean(self):
        cleaned_data = super().clean()
        is_active = cleaned_data.get("is_active")
        if self.instance.pk and self._was_active and not is_active:
            if self.instance.appointments.filter(
                status=Appointment.Status.CONFIRMED,
                starts_at__gte=timezone.now(),
            ).exists():
                self.add_error(
                    "is_active",
                    "No puedes pausar una línea con citas futuras confirmadas.",
                )
        return cleaned_data

    def save(self, commit=True):
        line = super().save(commit=False)
        line.business = self.business
        if commit:
            line.full_clean()
            line.save()
        return line


class BusinessClosureForm(forms.ModelForm):
    class Meta:
        model = BusinessClosure
        fields = (
            "closure_type",
            "date_from",
            "date_to",
            "start_time",
            "end_time",
            "work_line",
            "internal_reason",
            "is_active",
        )
        labels = {
            "closure_type": "Tipo",
            "date_from": "Desde",
            "date_to": "Hasta",
            "start_time": "Hora inicio",
            "end_time": "Hora fin",
            "work_line": "Alcance",
            "internal_reason": "Motivo interno",
            "is_active": "Aplicar al calendario",
        }
        widgets = {
            "date_from": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "date_to": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "internal_reason": forms.TextInput(
                attrs={
                    "autocomplete": "off",
                    "placeholder": "Ej. Formación, vacaciones o gestión interna",
                }
            ),
        }

    def __init__(self, *args, business, created_by=None, **kwargs):
        self.business = business
        self.created_by = created_by
        super().__init__(*args, **kwargs)
        self.fields["is_active"].required = False
        self.fields["closure_type"].choices = CLOSURE_TYPE_CHOICES
        self.fields["work_line"].queryset = business.work_lines.order_by("display_order", "line_number", "pk")
        self.fields["work_line"].empty_label = "Todo el negocio"

    def clean_internal_reason(self):
        return (self.cleaned_data.get("internal_reason") or "").strip()

    def clean_work_line(self):
        work_line = self.cleaned_data.get("work_line")
        if work_line is not None and work_line.business_id != self.business.id:
            raise forms.ValidationError("La línea debe pertenecer a este negocio.")
        return work_line

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")

        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "La fecha final debe ser igual o posterior a la inicial.")
        if bool(start_time) != bool(end_time):
            self.add_error(None, "Para bloquear solo unas horas, indica inicio y fin.")
        if start_time and end_time and start_time >= end_time:
            self.add_error("end_time", "La hora final debe ser posterior al inicio.")
        return cleaned_data

    def save(self, commit=True):
        closure = super().save(commit=False)
        closure.business = self.business
        if closure.pk is None and self.created_by is not None:
            closure.created_by = self.created_by
        if commit:
            closure.full_clean()
            closure.save()
        return closure


class SlotSelectionMixin(forms.Form):
    selected_work_line_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    selected_starts_at = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, require_slot=False, **kwargs):
        self.require_slot = require_slot
        super().__init__(*args, **kwargs)

    def clean_selected_starts_at(self):
        value = (self.cleaned_data.get("selected_starts_at") or "").strip()
        if not value:
            if self.require_slot:
                raise forms.ValidationError("Elige un hueco para confirmar la cita.")
            return None
        parsed = parse_datetime(value)
        if parsed is None:
            raise forms.ValidationError("El hueco seleccionado no es válido.")
        return parsed

    def clean(self):
        cleaned_data = super().clean()
        if self.require_slot and not cleaned_data.get("selected_work_line_id"):
            self.add_error("selected_work_line_id", "Elige una línea para confirmar la cita.")
        return cleaned_data


class PublicBookingForm(SlotSelectionMixin):
    services = forms.ModelMultipleChoiceField(
        label="Servicios",
        queryset=Service.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )
    target_date = forms.DateField(
        label="Día",
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )

    def __init__(self, *args, business, **kwargs):
        super().__init__(*args, **kwargs)
        self.business = business
        self.fields["services"].queryset = Service.objects.filter(
            business=business,
            is_active=True,
        ).order_by("display_order", "name", "pk")
        self.fields["services"].label_from_instance = (
            lambda service: f"{service.name} - {service.duration_minutes} min"
        )

    def clean_services(self):
        services = self.cleaned_data["services"]
        if not services:
            raise forms.ValidationError("Selecciona al menos un servicio.")
        return services

    def clean(self):
        cleaned_data = super().clean()
        services = cleaned_data.get("services")
        if services:
            cleaned_data["final_duration_minutes"] = sum(
                service.duration_minutes for service in services
            )

        return cleaned_data

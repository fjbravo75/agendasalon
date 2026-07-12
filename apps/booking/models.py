from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class BusinessCalendarSettings(models.Model):
    """Calendar configuration for one business."""

    business = models.OneToOneField(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="calendar_settings",
        verbose_name="negocio",
    )
    slot_interval_minutes = models.PositiveSmallIntegerField(
        "intervalo de hueco",
        default=15,
    )
    apply_national_holidays = models.BooleanField(
        "aplicar festivos nacionales",
        default=True,
    )

    class Meta:
        verbose_name = "configuración de calendario"
        verbose_name_plural = "configuraciones de calendario"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(slot_interval_minutes__gt=0),
                name="calendar_slot_interval_positive",
            )
        ]

    def clean(self):
        super().clean()
        if self.slot_interval_minutes is None:
            return
        if self.slot_interval_minutes <= 0:
            raise ValidationError({"slot_interval_minutes": "El intervalo debe ser positivo."})
        if self.slot_interval_minutes % 15 != 0:
            raise ValidationError(
                {"slot_interval_minutes": "El intervalo debe ser compatible con tramos de 15 minutos."}
            )

    def __str__(self):
        return f"Calendario de {self.business}"


class AvailabilityRule(models.Model):
    """Weekly working availability for a business."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Lunes"
        TUESDAY = 1, "Martes"
        WEDNESDAY = 2, "Miércoles"
        THURSDAY = 3, "Jueves"
        FRIDAY = 4, "Viernes"
        SATURDAY = 5, "Sábado"
        SUNDAY = 6, "Domingo"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="availability_rules",
        verbose_name="negocio",
    )
    weekday = models.PositiveSmallIntegerField("día de semana", choices=Weekday.choices)
    start_time = models.TimeField("hora inicio")
    end_time = models.TimeField("hora fin")
    is_active = models.BooleanField("activa", default=True)

    class Meta:
        verbose_name = "regla de disponibilidad"
        verbose_name_plural = "reglas de disponibilidad"
        ordering = ["business__commercial_name", "weekday", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(start_time__lt=models.F("end_time")),
                name="availability_start_before_end",
            )
        ]
        indexes = [
            models.Index(fields=["business", "weekday", "is_active"], name="availability_business_day_idx"),
        ]

    def clean(self):
        super().clean()
        if not self.start_time or not self.end_time:
            return
        if self.start_time >= self.end_time:
            raise ValidationError({"end_time": "La hora de fin debe ser posterior al inicio."})

        if self.business_id and self.is_active:
            overlaps = AvailabilityRule.objects.filter(
                business_id=self.business_id,
                weekday=self.weekday,
                is_active=True,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            if self.pk:
                overlaps = overlaps.exclude(pk=self.pk)
            if overlaps.exists():
                raise ValidationError("La disponibilidad se solapa con otra regla activa.")

    def __str__(self):
        return f"{self.business} - {self.get_weekday_display()} {self.start_time}-{self.end_time}"


class WorkLine(models.Model):
    """Operational line, representing simultaneous capacity, not an employee."""

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="work_lines",
        verbose_name="negocio",
    )
    line_number = models.PositiveSmallIntegerField("número de línea")
    name = models.CharField("nombre", max_length=80, blank=True)
    is_active = models.BooleanField("activa", default=True)
    display_order = models.PositiveSmallIntegerField("orden", default=0)

    class Meta:
        verbose_name = "línea de trabajo"
        verbose_name_plural = "líneas de trabajo"
        ordering = ["business__commercial_name", "display_order", "line_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "line_number"],
                name="unique_business_work_line_number",
            ),
            models.CheckConstraint(
                condition=models.Q(line_number__gte=1, line_number__lte=3),
                name="work_line_number_between_1_and_3",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "is_active"], name="workline_business_active_idx"),
        ]

    def clean(self):
        super().clean()
        if self.line_number is None:
            return
        if not 1 <= self.line_number <= 3:
            raise ValidationError({"line_number": "La línea debe estar entre 1 y 3."})

    def __str__(self):
        return self.name or f"Línea {self.line_number}"


class Service(models.Model):
    """Service offered by a business and usable inside appointments."""

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="services",
        verbose_name="negocio",
    )
    name = models.CharField("nombre", max_length=140)
    description = models.TextField("descripción", blank=True)
    duration_minutes = models.PositiveIntegerField("duración en minutos")
    price_amount = models.DecimalField(
        "precio orientativo",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    color_hex = models.CharField("color", max_length=7, blank=True)
    is_active = models.BooleanField("activo", default=True)
    display_order = models.PositiveSmallIntegerField("orden", default=0)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "servicio"
        verbose_name_plural = "servicios"
        ordering = ["business__commercial_name", "display_order", "name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name="service_duration_positive",
            )
        ]
        indexes = [
            models.Index(fields=["business", "is_active"], name="service_business_active_idx"),
            models.Index(fields=["business", "display_order"], name="service_business_order_idx"),
        ]

    def clean(self):
        super().clean()
        if self.duration_minutes is None:
            return
        if self.duration_minutes <= 0:
            raise ValidationError({"duration_minutes": "La duración debe ser positiva."})
        if self.duration_minutes % 15 != 0:
            raise ValidationError(
                {"duration_minutes": "La duración debe ser compatible con tramos de 15 minutos."}
            )
        if self.color_hex and not self.color_hex.startswith("#"):
            raise ValidationError({"color_hex": "El color debe usar formato hexadecimal, por ejemplo #C56B5C."})

    def __str__(self):
        return f"{self.name} ({self.business})"


class BusinessClosure(models.Model):
    """Closure, holiday override or punctual block in the business calendar."""

    class ClosureType(models.TextChoices):
        VACATION = "vacaciones", "Vacaciones"
        LOCAL_HOLIDAY = "festivo_local", "Festivo local"
        REGIONAL_MANUAL_HOLIDAY = "festivo_autonomico_manual", "Festivo autonómico manual"
        PUNCTUAL_BLOCK = "bloqueo_puntual", "Bloqueo puntual"
        BUSINESS_CLOSURE = "cierre_negocio", "Cierre de negocio"
        OTHER = "otro", "Otro"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="closures",
        verbose_name="negocio",
    )
    work_line = models.ForeignKey(
        WorkLine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="closures",
        verbose_name="línea de trabajo",
    )
    date_from = models.DateField("fecha desde")
    date_to = models.DateField("fecha hasta")
    start_time = models.TimeField("hora inicio", null=True, blank=True)
    end_time = models.TimeField("hora fin", null=True, blank=True)
    closure_type = models.CharField(
        "tipo",
        max_length=40,
        choices=ClosureType.choices,
        default=ClosureType.OTHER,
    )
    internal_reason = models.CharField("motivo interno", max_length=255, blank=True)
    is_active = models.BooleanField("activo", default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_business_closures",
        verbose_name="creado por",
    )
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "cierre o bloqueo"
        verbose_name_plural = "cierres y bloqueos"
        ordering = ["business__commercial_name", "-date_from", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(date_from__lte=models.F("date_to")),
                name="closure_date_from_before_date_to",
            )
        ]
        indexes = [
            models.Index(fields=["business", "is_active"], name="closure_business_active_idx"),
            models.Index(fields=["business", "date_from", "date_to"], name="closure_business_dates_idx"),
        ]

    def clean(self):
        super().clean()
        if not self.date_from or not self.date_to:
            return
        if self.date_from > self.date_to:
            raise ValidationError({"date_to": "La fecha final debe ser igual o posterior a la inicial."})
        if bool(self.start_time) != bool(self.end_time):
            raise ValidationError("Para un cierre parcial deben informarse hora inicio y hora fin.")
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({"end_time": "La hora de fin debe ser posterior al inicio."})
        if self.work_line_id and self.business_id and self.work_line.business_id != self.business_id:
            raise ValidationError({"work_line": "La línea debe pertenecer al mismo negocio."})

    def __str__(self):
        scope = self.work_line or "negocio completo"
        return f"{self.business} - {scope} - {self.date_from}"


class Appointment(models.Model):
    """Appointment with an explicit operational outcome."""

    class Status(models.TextChoices):
        CONFIRMED = "confirmada", "Confirmada"
        CANCELLED = "cancelada", "Cancelada"
        COMPLETED = "completada", "Atendida"
        NO_SHOW = "no_presentada", "No se presentó"

    class ManualChannel(models.TextChoices):
        PHONE = "telefono", "Teléfono"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Email"
        FRONT_DESK = "mostrador", "Mostrador"
        PUBLIC_WEB = "web_publica", "Reserva online"
        OTHER = "otro", "Otro"

    business = models.ForeignKey(
        "businesses.Business",
        on_delete=models.CASCADE,
        related_name="appointments",
        verbose_name="negocio",
    )
    business_client = models.ForeignKey(
        "customers.BusinessClient",
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name="ficha de cliente",
    )
    work_line = models.ForeignKey(
        WorkLine,
        on_delete=models.PROTECT,
        related_name="appointments",
        verbose_name="línea de trabajo",
    )
    starts_at = models.DateTimeField("inicio")
    ends_at = models.DateTimeField("fin")
    total_duration_minutes = models.PositiveIntegerField("duración total")
    duration_adjustment_reason = models.CharField(
        "motivo de ajuste de duración",
        max_length=255,
        blank=True,
    )
    status = models.CharField(
        "estado",
        max_length=20,
        choices=Status.choices,
        default=Status.CONFIRMED,
    )
    manual_channel = models.CharField(
        "canal de origen",
        max_length=20,
        choices=ManualChannel.choices,
        default=ManualChannel.PHONE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_appointments",
        verbose_name="creado por",
    )
    requested_by_client_access = models.ForeignKey(
        "customers.BusinessClientAccess",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_appointments",
        verbose_name="cuenta que solicitó la cita",
    )
    requested_by_name_snapshot = models.CharField(
        "nombre de quien solicitó la cita",
        max_length=160,
        blank=True,
    )
    requested_by_relationship_snapshot = models.CharField(
        "relación de quien solicitó la cita",
        max_length=80,
        blank=True,
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cancelled_appointments",
        verbose_name="cancelado por",
    )
    cancelled_at = models.DateTimeField("fecha de cancelación", null=True, blank=True)
    cancellation_reason = models.CharField("motivo de cancelación", max_length=255, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_appointments",
        verbose_name="completado por",
    )
    completed_at = models.DateTimeField("fecha de completado", null=True, blank=True)
    no_show_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="no_show_appointments",
        verbose_name="ausencia marcada por",
    )
    no_show_marked_at = models.DateTimeField("fecha de ausencia", null=True, blank=True)
    service_summary_snapshot = models.TextField("resumen snapshot de servicios", blank=True)
    created_at = models.DateTimeField("fecha de alta", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "cita"
        verbose_name_plural = "citas"
        ordering = ["-starts_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(starts_at__lt=models.F("ends_at")),
                name="appointment_starts_before_ends",
            ),
            models.CheckConstraint(
                condition=models.Q(total_duration_minutes__gt=0),
                name="appointment_duration_positive",
            ),
        ]
        indexes = [
            models.Index(fields=["business", "starts_at"], name="appointment_business_start_idx"),
            models.Index(fields=["work_line", "starts_at", "ends_at"], name="appointment_line_time_idx"),
            models.Index(fields=["business", "status"], name="appt_business_status_idx"),
            models.Index(fields=["manual_channel"], name="appointment_channel_idx"),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.starts_at and timezone.is_naive(self.starts_at):
            errors["starts_at"] = "La fecha de inicio debe ser timezone-aware."
        if self.ends_at and timezone.is_naive(self.ends_at):
            errors["ends_at"] = "La fecha de fin debe ser timezone-aware."
        if self.starts_at and self.ends_at:
            if self.starts_at >= self.ends_at:
                errors["ends_at"] = "La fecha de fin debe ser posterior al inicio."
            actual_minutes = int((self.ends_at - self.starts_at).total_seconds() // 60)
            if self.total_duration_minutes is not None and actual_minutes != self.total_duration_minutes:
                errors["total_duration_minutes"] = "La duración total debe coincidir con inicio y fin."
        if self.total_duration_minutes is None:
            pass
        elif self.total_duration_minutes <= 0:
            errors["total_duration_minutes"] = "La duración total debe ser positiva."
        elif self.total_duration_minutes % 15 != 0:
            errors["total_duration_minutes"] = "La duración total debe usar tramos de 15 minutos."
        if self.business_client_id and self.business_id:
            if self.business_client.business_id != self.business_id:
                errors["business_client"] = "La ficha debe pertenecer al mismo negocio."
        if self.requested_by_client_access_id and self.business_id:
            if self.requested_by_client_access.business_id != self.business_id:
                errors["requested_by_client_access"] = "La cuenta debe pertenecer al mismo negocio."
        if self.work_line_id and self.business_id:
            if self.work_line.business_id != self.business_id:
                errors["work_line"] = "La línea debe pertenecer al mismo negocio."
            elif not self.work_line.is_active and self.status == self.Status.CONFIRMED:
                errors["work_line"] = "La línea debe estar activa para citas confirmadas."
        if self.status in {self.Status.COMPLETED, self.Status.NO_SHOW} and self.starts_at:
            if self.starts_at > timezone.now():
                errors["status"] = "Una cita futura no puede cerrarse."

        if (
            not errors
            and self.status == self.Status.CONFIRMED
            and self.work_line_id
            and self.starts_at
            and self.ends_at
        ):
            overlaps = Appointment.objects.filter(
                work_line_id=self.work_line_id,
                status=self.Status.CONFIRMED,
                starts_at__lt=self.ends_at,
                ends_at__gt=self.starts_at,
            )
            if self.pk:
                overlaps = overlaps.exclude(pk=self.pk)
            if overlaps.exists():
                errors["starts_at"] = "Ya existe una cita confirmada en esa línea y tramo."

        if self.pk and self.appointment_services.exists():
            services_minutes = self.services_duration_sum()
            if services_minutes != self.total_duration_minutes and not self.duration_adjustment_reason.strip():
                errors["duration_adjustment_reason"] = (
                    "Debe indicarse motivo si la duración difiere de los servicios."
                )

        if errors:
            raise ValidationError(errors)

    def services_duration_sum(self):
        return sum(
            item.duration_minutes_snapshot
            for item in self.appointment_services.all()
        )

    def is_pending_closure(self, *, at=None):
        """Return whether the elapsed appointment still needs a real outcome."""
        at = at or timezone.now()
        return self.status == self.Status.CONFIRMED and self.ends_at <= at

    def __str__(self):
        return f"{self.business_client} - {self.starts_at:%Y-%m-%d %H:%M}"


class AppointmentService(models.Model):
    """Service snapshot inside an appointment."""

    appointment = models.ForeignKey(
        Appointment,
        on_delete=models.CASCADE,
        related_name="appointment_services",
        verbose_name="cita",
    )
    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="appointment_services",
        verbose_name="servicio original",
    )
    display_order = models.PositiveSmallIntegerField("orden", default=0)
    service_name_snapshot = models.CharField("nombre snapshot", max_length=140)
    duration_minutes_snapshot = models.PositiveIntegerField("duración snapshot")
    price_amount_snapshot = models.DecimalField(
        "precio snapshot",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
    )
    color_hex_snapshot = models.CharField("color snapshot", max_length=7, blank=True)

    class Meta:
        verbose_name = "servicio de cita"
        verbose_name_plural = "servicios de cita"
        ordering = ["appointment", "display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["appointment", "display_order"],
                name="unique_appointment_service_order",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes_snapshot__gt=0),
                name="appointment_service_duration_positive",
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.appointment_id and self.service_id:
            if self.service.business_id != self.appointment.business_id:
                errors["service"] = "El servicio debe pertenecer al mismo negocio que la cita."
        if self.duration_minutes_snapshot is None:
            pass
        elif self.duration_minutes_snapshot <= 0:
            errors["duration_minutes_snapshot"] = "La duración debe ser positiva."
        elif self.duration_minutes_snapshot % 15 != 0:
            errors["duration_minutes_snapshot"] = "La duración debe usar tramos de 15 minutos."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.service_id:
            if not self.service_name_snapshot:
                self.service_name_snapshot = self.service.name
            if not self.duration_minutes_snapshot:
                self.duration_minutes_snapshot = self.service.duration_minutes
            if self.price_amount_snapshot is None:
                self.price_amount_snapshot = self.service.price_amount
            if not self.color_hex_snapshot:
                self.color_hex_snapshot = self.service.color_hex
        super().save(*args, **kwargs)

    def __str__(self):
        return self.service_name_snapshot

# Create your models here.

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.functional import cached_property


# Two BOE responses have 20-second read timeouts; 15 minutes also leaves ample
# room for parsing and transactional reconciliation before a run is considered stale.
HOLIDAY_SYNC_INTERRUPTED_AFTER = timedelta(minutes=15)


class OfficialHoliday(models.Model):
    """Global official holiday with traceability."""

    class Scope(models.TextChoices):
        NATIONAL = "nacional", "Nacional"
        REGIONAL = "autonomico", "Autonómico"
        LOCAL = "local", "Local"

    date = models.DateField("fecha")
    name = models.CharField("nombre", max_length=180)
    scope = models.CharField("ámbito", max_length=40, choices=Scope.choices)
    year = models.PositiveSmallIntegerField("año")
    source_name = models.CharField("fuente", max_length=160)
    source_url = models.URLField("url fuente", blank=True)
    official_reference = models.CharField("referencia oficial", max_length=180, blank=True)
    loaded_at = models.DateTimeField("fecha de carga", auto_now_add=True)
    updated_at = models.DateTimeField("última actualización", auto_now=True)

    class Meta:
        verbose_name = "festivo oficial"
        verbose_name_plural = "festivos oficiales"
        ordering = ["date", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "scope"],
                name="unique_official_holiday",
            )
        ]
        indexes = [
            models.Index(fields=["year", "scope"], name="holiday_year_scope_idx"),
            models.Index(fields=["date"], name="holiday_date_idx"),
        ]

    def __str__(self):
        return f"{self.date} - {self.name}"


class HolidaySyncRun(models.Model):
    """Trace of a holiday loading run."""

    class Status(models.TextChoices):
        SUCCESS = "success", "Correcta"
        FAILED = "failed", "Fallida"
        PARTIAL = "partial", "Parcial"

    year = models.PositiveSmallIntegerField("año")
    source_name = models.CharField("fuente", max_length=160)
    source_url = models.URLField("url fuente", blank=True)
    official_reference = models.CharField("referencia oficial", max_length=180, blank=True)
    status = models.CharField("estado", max_length=20, choices=Status.choices)
    started_at = models.DateTimeField("inicio")
    finished_at = models.DateTimeField("fin", null=True, blank=True)
    items_loaded = models.PositiveIntegerField("elementos cargados", default=0)
    items_created = models.PositiveIntegerField("elementos creados", default=0)
    items_updated = models.PositiveIntegerField("elementos actualizados", default=0)
    items_removed = models.PositiveIntegerField("elementos retirados", default=0)
    items_skipped = models.PositiveIntegerField("elementos conservados", default=0)
    affected_appointments = models.PositiveIntegerField("citas afectadas", default=0)
    affected_businesses = models.PositiveIntegerField("negocios afectados", default=0)
    error_detail = models.TextField("detalle de error", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="holiday_sync_runs",
        verbose_name="creado por",
    )

    class Meta:
        verbose_name = "carga de festivos"
        verbose_name_plural = "cargas de festivos"
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["year", "status"], name="holiday_run_year_status_idx"),
        ]

    def clean(self):
        super().clean()
        if self.finished_at and self.started_at and self.finished_at < self.started_at:
            from django.core.exceptions import ValidationError

            raise ValidationError({"finished_at": "La fecha de fin no puede ser anterior al inicio."})

    @cached_property
    def presentation_is_interrupted(self):
        return (
            self.finished_at is None
            and self.started_at
            <= timezone.now() - HOLIDAY_SYNC_INTERRUPTED_AFTER
        )

    @property
    def presentation_status(self):
        if self.finished_at is not None:
            return self.get_status_display()
        if self.presentation_is_interrupted:
            return "Interrumpida"
        return "En curso"

    @property
    def presentation_is_danger(self):
        return self.presentation_is_interrupted or (
            self.finished_at is not None and self.status == self.Status.FAILED
        )

    def __str__(self):
        return f"{self.year} - {self.source_name} ({self.presentation_status})"

# Create your models here.

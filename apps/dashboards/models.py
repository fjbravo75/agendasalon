
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class BackupExecution(models.Model):
    """Registro técnico global de copias, sin rutas, credenciales ni datos personales."""

    class Status(models.TextChoices):
        RUNNING = "running", "En curso"
        SUCCEEDED = "succeeded", "Correcta"
        FAILED = "failed", "Fallida"

    class Destination(models.TextChoices):
        LOCAL = "local", "Almacenamiento local"
        EXTERNAL_ENCRYPTED = "external_encrypted", "Destino externo cifrado"

    status = models.CharField(
        "estado",
        max_length=16,
        choices=Status.choices,
        default=Status.RUNNING,
    )
    destination = models.CharField(
        "destino",
        max_length=24,
        choices=Destination.choices,
        default=Destination.LOCAL,
    )
    started_at = models.DateTimeField("inicio", default=timezone.now)
    finished_at = models.DateTimeField("fin", null=True, blank=True)
    database_included = models.BooleanField("base de datos incluida", default=False)
    media_included = models.BooleanField("archivos incluidos", default=False)
    integrity_verified = models.BooleanField("integridad verificada", default=False)
    authenticity_verified = models.BooleanField("autenticidad verificada", default=False)
    total_size_bytes = models.BigIntegerField(
        "tamaño total en bytes",
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    failure_code = models.CharField(
        "código de fallo",
        max_length=48,
        blank=True,
        help_text="Código técnico acotado; nunca contiene el error original ni secretos.",
    )

    class Meta:
        verbose_name = "ejecución de copia"
        verbose_name_plural = "ejecuciones de copia"
        ordering = ("-started_at", "-id")
        indexes = [
            models.Index(fields=("-started_at",), name="backup_exec_recent_idx"),
            models.Index(fields=("status", "-started_at"), name="backup_exec_status_idx"),
        ]

    def __str__(self):
        return f"{self.get_status_display()} · {self.started_at:%Y-%m-%d %H:%M}"

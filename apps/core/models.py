from django.db import models
from django.utils import timezone


DEMO_REFRESH_RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$"
DEMO_REFRESH_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"


class DemoRefreshReceipt(models.Model):
    """Recibo de una regeneración demo confirmada por PostgreSQL."""

    run_id = models.CharField("identificador de ejecución", max_length=128, unique=True)
    base_date = models.DateField("fecha base")
    fingerprint = models.CharField("huella del escenario", max_length=64)
    completed_at = models.DateTimeField("finalización", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "recibo de regeneración demo"
        verbose_name_plural = "recibos de regeneración demo"
        ordering = ("-completed_at", "-id")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(run_id__regex=DEMO_REFRESH_RUN_ID_PATTERN),
                name="demo_refresh_receipt_run_id_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(fingerprint__regex=DEMO_REFRESH_FINGERPRINT_PATTERN),
                name="demo_refresh_receipt_fingerprint_valid",
            ),
        ]

    def __str__(self):
        return f"{self.run_id} · {self.base_date}"


class SecurityThrottle(models.Model):
    """Contador de seguridad sin almacenar identificadores personales en claro."""

    scope = models.CharField("ámbito", max_length=64)
    key_digest = models.CharField("resumen del identificador", max_length=64)
    attempts = models.PositiveIntegerField("intentos", default=0)
    window_started_at = models.DateTimeField("inicio de ventana")
    blocked_until = models.DateTimeField("bloqueado hasta", null=True, blank=True)
    last_attempt_at = models.DateTimeField("último intento")

    class Meta:
        verbose_name = "límite de seguridad"
        verbose_name_plural = "límites de seguridad"
        constraints = [
            models.UniqueConstraint(
                fields=["scope", "key_digest"],
                name="unique_security_throttle_scope_key",
            )
        ]
        indexes = [
            models.Index(fields=["scope", "blocked_until"], name="security_throttle_block_idx"),
        ]

    def __str__(self):
        return f"{self.scope}: {self.attempts} intentos"

# Create your models here.

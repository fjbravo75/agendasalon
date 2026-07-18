from uuid import uuid4

from django.conf import settings
from django.db import models
from django.utils import timezone


DEMO_REFRESH_RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$"
DEMO_REFRESH_FINGERPRINT_PATTERN = r"^[0-9a-f]{64}$"
DEMO_REFRESH_FAILURE_CODE_PATTERN = r"^[a-z0-9_]{1,48}$"


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


class DemoRefreshRequest(models.Model):
    """Petición mínima y auditable de una regeneración manual de la demo."""

    class Status(models.TextChoices):
        PENDING = "pending", "Solicitada"
        PROCESSING = "processing", "En curso"
        COMPLETED = "completed", "Completada"
        FAILED = "failed", "Fallida"
        CANCELLED = "cancelled", "Cancelada"

    public_id = models.UUIDField(
        "identificador público",
        default=uuid4,
        unique=True,
        editable=False,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="demo_refresh_requests",
        verbose_name="solicitante",
    )
    base_date = models.DateField("fecha base")
    status = models.CharField(
        "estado",
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    requested_at = models.DateTimeField("solicitud", default=timezone.now, editable=False)
    started_at = models.DateTimeField("inicio", null=True, blank=True)
    finished_at = models.DateTimeField("fin", null=True, blank=True)
    receipt = models.OneToOneField(
        DemoRefreshReceipt,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="manual_requests",
        verbose_name="recibo técnico",
    )
    failure_code = models.CharField(
        "código de fallo",
        max_length=48,
        blank=True,
        help_text="Código acotado sin errores originales, rutas, secretos ni datos personales.",
    )
    origin_digest = models.CharField("resumen criptográfico de origen", max_length=64)

    class Meta:
        verbose_name = "solicitud de regeneración demo"
        verbose_name_plural = "solicitudes de regeneración demo"
        ordering = ("-requested_at", "-id")
        indexes = [
            models.Index(fields=("status", "requested_at"), name="demo_refresh_req_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                models.Value(1),
                condition=models.Q(status__in=("pending", "processing")),
                name="unique_active_demo_refresh_request",
            ),
            models.CheckConstraint(
                condition=models.Q(origin_digest__regex=DEMO_REFRESH_FINGERPRINT_PATTERN),
                name="demo_refresh_request_origin_digest_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(failure_code="")
                    | models.Q(failure_code__regex=DEMO_REFRESH_FAILURE_CODE_PATTERN)
                ),
                name="demo_refresh_request_failure_code_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="pending",
                        started_at__isnull=True,
                        finished_at__isnull=True,
                        receipt__isnull=True,
                        failure_code="",
                    )
                    | models.Q(
                        status="processing",
                        started_at__isnull=False,
                        finished_at__isnull=True,
                        receipt__isnull=True,
                        failure_code="",
                    )
                    | models.Q(
                        status="completed",
                        started_at__isnull=False,
                        finished_at__isnull=False,
                        receipt__isnull=False,
                        failure_code="",
                    )
                    | models.Q(
                        status="failed",
                        started_at__isnull=False,
                        finished_at__isnull=False,
                    )
                    & ~models.Q(failure_code="")
                    | models.Q(
                        status="cancelled",
                        finished_at__isnull=False,
                        receipt__isnull=True,
                        failure_code="",
                    )
                ),
                name="demo_refresh_request_state_valid",
            ),
        ]

    def __str__(self):
        return f"{self.get_status_display()} · {self.base_date} · {self.public_id}"


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

from django.db import models


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

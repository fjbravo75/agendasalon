import hashlib
import hmac
import ipaddress
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import SecurityThrottle
from apps.core.phone import normalize_phone


THROTTLE_MESSAGE = "Demasiados intentos. Espera unos minutos antes de volver a intentarlo."


def request_ip(request):
    """Resuelve la IP sin confiar en cabeceras salvo tras proxies declarados."""

    remote_addr = request.META.get("REMOTE_ADDR") or "desconocida"
    trusted_proxies = getattr(settings, "TRUSTED_PROXY_IPS", set())
    if remote_addr not in trusted_proxies:
        return remote_addr

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidates = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    candidates.append(remote_addr)
    for candidate in reversed(candidates):
        if candidate in trusted_proxies:
            continue
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return remote_addr


def phone_throttle_key(value):
    """Unifica formatos equivalentes para que no sirvan para eludir el límite."""

    try:
        return normalize_phone(value)
    except (TypeError, ValidationError):
        return str(value or "").strip().lower()


def throttle_key_digest(value):
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        str(value).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_throttled(*, scope, key, now=None):
    now = now or timezone.now()
    throttle = SecurityThrottle.objects.filter(
        scope=scope,
        key_digest=throttle_key_digest(key),
    ).only("blocked_until").first()
    return bool(throttle and throttle.blocked_until and throttle.blocked_until > now)


@transaction.atomic
def record_failed_attempt(*, scope, key, limit, window_seconds, now=None):
    now = now or timezone.now()
    digest = throttle_key_digest(key)
    try:
        throttle = SecurityThrottle.objects.select_for_update().get(
            scope=scope,
            key_digest=digest,
        )
    except SecurityThrottle.DoesNotExist:
        try:
            with transaction.atomic():
                throttle = SecurityThrottle.objects.create(
                    scope=scope,
                    key_digest=digest,
                    attempts=0,
                    window_started_at=now,
                    last_attempt_at=now,
                )
        except IntegrityError:
            throttle = SecurityThrottle.objects.select_for_update().get(
                scope=scope,
                key_digest=digest,
            )

    window = timedelta(seconds=window_seconds)
    if now - throttle.window_started_at >= window:
        throttle.attempts = 0
        throttle.window_started_at = now
        throttle.blocked_until = None

    throttle.attempts += 1
    throttle.last_attempt_at = now
    if throttle.attempts >= limit:
        throttle.blocked_until = now + window
    throttle.save(
        update_fields=[
            "attempts",
            "window_started_at",
            "blocked_until",
            "last_attempt_at",
        ]
    )
    return bool(throttle.blocked_until and throttle.blocked_until > now)


def clear_failed_attempts(*, scope, key):
    SecurityThrottle.objects.filter(
        scope=scope,
        key_digest=throttle_key_digest(key),
    ).delete()

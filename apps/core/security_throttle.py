import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.core.models import SecurityThrottle
from apps.core.phone import normalize_phone


THROTTLE_MESSAGE = "Demasiados intentos. Espera unos minutos antes de volver a intentarlo."


@dataclass(frozen=True)
class ThrottleLimit:
    scope: str
    key: str
    limit: int
    window_seconds: int

    def __post_init__(self):
        if self.limit < 1 or self.window_seconds < 1:
            raise ValueError("Los límites de seguridad deben ser positivos.")


@dataclass(frozen=True)
class ReservedThrottle:
    scope: str
    key_digest: str
    limit: int
    window_started_at: object
    attempts_after_reservation: int


@dataclass(frozen=True)
class ThrottleReservation:
    allowed: bool
    entries: tuple[ReservedThrottle, ...] = ()
    blocked_scopes: frozenset[str] = frozenset()


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


def _get_locked_throttle(*, scope, digest, now):
    try:
        return SecurityThrottle.objects.select_for_update().get(
            scope=scope,
            key_digest=digest,
        )
    except SecurityThrottle.DoesNotExist:
        try:
            with transaction.atomic():
                return SecurityThrottle.objects.create(
                    scope=scope,
                    key_digest=digest,
                    attempts=0,
                    window_started_at=now,
                    last_attempt_at=now,
                )
        except IntegrityError:
            return SecurityThrottle.objects.select_for_update().get(
                scope=scope,
                key_digest=digest,
            )


@transaction.atomic
def reserve_throttle_attempts(*, limits, now=None):
    """Reserva capacidad antes del trabajo protegido y bloquea todas las claves en orden."""

    now = now or timezone.now()
    prepared_limits = sorted(
        (
            limit.scope,
            throttle_key_digest(limit.key),
            limit.limit,
            limit.window_seconds,
        )
        for limit in limits
    )
    identities = [(scope, digest) for scope, digest, _limit, _window in prepared_limits]
    if len(identities) != len(set(identities)):
        raise ValueError("No se puede reservar dos veces el mismo límite de seguridad.")

    locked_limits = []
    for scope, digest, limit, window_seconds in prepared_limits:
        throttle = _get_locked_throttle(scope=scope, digest=digest, now=now)
        window = timedelta(seconds=window_seconds)
        if now - throttle.window_started_at >= window:
            throttle.attempts = 0
            throttle.window_started_at = now
            throttle.blocked_until = None
        locked_limits.append((throttle, limit, window))

    blocked_scopes = frozenset(
        throttle.scope
        for throttle, limit, _window in locked_limits
        if (throttle.blocked_until and throttle.blocked_until > now)
        or throttle.attempts >= limit
    )
    if blocked_scopes:
        return ThrottleReservation(allowed=False, blocked_scopes=blocked_scopes)

    entries = []
    newly_blocked_scopes = set()
    for throttle, limit, window in locked_limits:
        throttle.attempts += 1
        throttle.last_attempt_at = now
        if throttle.attempts >= limit:
            throttle.blocked_until = now + window
            newly_blocked_scopes.add(throttle.scope)
        throttle.save(
            update_fields=[
                "attempts",
                "window_started_at",
                "blocked_until",
                "last_attempt_at",
            ]
        )
        entries.append(
            ReservedThrottle(
                scope=throttle.scope,
                key_digest=throttle.key_digest,
                limit=limit,
                window_started_at=throttle.window_started_at,
                attempts_after_reservation=throttle.attempts,
            )
        )
    return ThrottleReservation(
        allowed=True,
        entries=tuple(entries),
        blocked_scopes=frozenset(newly_blocked_scopes),
    )


@transaction.atomic
def settle_successful_throttle(reservation, *, reset_scopes=()):
    """Retira la reserva correcta tras un acceso válido sin borrar fallos posteriores."""

    if not reservation.allowed:
        return
    reset_scopes = set(reset_scopes)
    for entry in sorted(reservation.entries, key=lambda item: (item.scope, item.key_digest)):
        try:
            throttle = SecurityThrottle.objects.select_for_update().get(
                scope=entry.scope,
                key_digest=entry.key_digest,
            )
        except SecurityThrottle.DoesNotExist:
            continue
        if throttle.window_started_at != entry.window_started_at:
            continue
        if entry.scope in reset_scopes:
            throttle.attempts = max(
                0,
                throttle.attempts - entry.attempts_after_reservation,
            )
        else:
            throttle.attempts = max(0, throttle.attempts - 1)
        if throttle.attempts < entry.limit:
            throttle.blocked_until = None
        if throttle.attempts == 0:
            throttle.delete()
        else:
            throttle.save(update_fields=["attempts", "blocked_until"])


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

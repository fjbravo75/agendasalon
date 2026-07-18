from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.core.models import (
    DEMO_REFRESH_FINGERPRINT_PATTERN,
    DemoRefreshReceipt,
    DemoRefreshRequest,
)
from apps.core.features import manual_demo_refresh_enabled
from apps.notifications.services import queue_operational_notice_on_commit


ACTIVE_STATUSES = (
    DemoRefreshRequest.Status.PENDING,
    DemoRefreshRequest.Status.PROCESSING,
)
FAILURE_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,48}$")
PROCESSING_STALE_AFTER = timedelta(minutes=60)


class DemoRefreshRequestUnavailable(Exception):
    """La acción no está disponible con el estado o configuración actuales."""


class ActiveDemoRefreshRequestExists(Exception):
    """Ya existe una petición pendiente o en curso."""


class DemoRefreshFinalizationError(Exception):
    """La petición no puede cerrarse con la evidencia disponible."""


@dataclass(frozen=True)
class DemoRefreshClaim:
    refresh_request: DemoRefreshRequest
    recovery_required: bool = False


def _require_single_active_superadmin(actor) -> None:
    User = get_user_model()
    active_superadmins = User._base_manager.filter(is_active=True, is_superuser=True)
    if not actor.is_authenticated or not actor.is_active or not actor.is_superuser:
        raise DemoRefreshRequestUnavailable
    if active_superadmins.count() != 1 or not active_superadmins.filter(pk=actor.pk).exists():
        raise DemoRefreshRequestUnavailable


def _queue_request_notice(refresh_request, *, code: str) -> None:
    queue_operational_notice_on_commit(
        scope="platform",
        code=code,
        deduplication_key=f"demo-refresh:{code}:{refresh_request.public_id}",
        action_path=reverse("dashboards:superadmin_continuity"),
        context={"request_id": str(refresh_request.public_id)},
    )


def request_demo_refresh(*, actor, origin_digest: str) -> DemoRefreshRequest:
    """Registra una petición; nunca ejecuta comandos ni eleva privilegios."""

    if not manual_demo_refresh_enabled():
        raise DemoRefreshRequestUnavailable
    _require_single_active_superadmin(actor)
    if not re.fullmatch(DEMO_REFRESH_FINGERPRINT_PATTERN, origin_digest or ""):
        raise DemoRefreshRequestUnavailable

    try:
        with transaction.atomic():
            if DemoRefreshRequest.objects.select_for_update().filter(
                status__in=ACTIVE_STATUSES
            ).exists():
                raise ActiveDemoRefreshRequestExists
            refresh_request = DemoRefreshRequest.objects.create(
                requested_by=actor,
                base_date=timezone.localdate(),
                origin_digest=origin_digest,
            )
            _queue_request_notice(refresh_request, code="demo_refresh_requested")
    except IntegrityError as exc:
        if DemoRefreshRequest.objects.filter(status__in=ACTIVE_STATUSES).exists():
            raise ActiveDemoRefreshRequestExists from exc
        raise
    return refresh_request


def claim_pending_demo_refresh(*, now=None) -> DemoRefreshClaim | None:
    """Reclama una sola petición bajo bloqueo para el despachador privilegiado."""

    if not manual_demo_refresh_enabled():
        raise DemoRefreshRequestUnavailable
    now = now or timezone.now()
    with transaction.atomic():
        active = (
            DemoRefreshRequest.objects.select_for_update()
            .filter(status__in=ACTIVE_STATUSES)
            .order_by("requested_at", "pk")
            .first()
        )
        if active is None:
            return None
        if active.status == DemoRefreshRequest.Status.PROCESSING:
            receipt = DemoRefreshReceipt.objects.filter(
                run_id=str(active.public_id),
                base_date=active.base_date,
                fingerprint__regex=DEMO_REFRESH_FINGERPRINT_PATTERN,
            ).first()
            if receipt is not None:
                # El despachador root decide si el runtime está íntegro antes de
                # finalizar. Django solo expone la reconciliación posible y no
                # vuelve a ejecutar nunca una operación destructiva.
                return DemoRefreshClaim(active, recovery_required=True)
            if active.started_at and now - active.started_at >= PROCESSING_STALE_AFTER:
                active.status = DemoRefreshRequest.Status.FAILED
                active.finished_at = now
                active.failure_code = "dispatcher_interrupted"
                active.save(
                    update_fields=(
                        "status",
                        "finished_at",
                        "failure_code",
                    )
                )
                _queue_request_notice(active, code="demo_refresh_failed")
            return None

        active.status = DemoRefreshRequest.Status.PROCESSING
        active.started_at = now
        active.save(update_fields=("status", "started_at"))
        return DemoRefreshClaim(active)


def finalize_demo_refresh(
    *,
    public_id,
    succeeded: bool,
    failure_code: str = "",
    now=None,
) -> DemoRefreshRequest:
    """Cierra una petición usando el recibo PostgreSQL del mismo UUID y fecha."""

    if not manual_demo_refresh_enabled():
        raise DemoRefreshRequestUnavailable
    now = now or timezone.now()
    with transaction.atomic():
        try:
            refresh_request = DemoRefreshRequest.objects.select_for_update().get(
                public_id=public_id
            )
        except (DemoRefreshRequest.DoesNotExist, ValidationError) as exc:
            raise DemoRefreshFinalizationError("request_missing") from exc
        if refresh_request.status != DemoRefreshRequest.Status.PROCESSING:
            raise DemoRefreshFinalizationError("request_not_processing")

        receipt = DemoRefreshReceipt.objects.filter(
            run_id=str(refresh_request.public_id),
            base_date=refresh_request.base_date,
        ).first()
        refresh_request.finished_at = now
        refresh_request.receipt = receipt

        if succeeded:
            if receipt is None or not re.fullmatch(
                DEMO_REFRESH_FINGERPRINT_PATTERN,
                receipt.fingerprint,
            ):
                raise DemoRefreshFinalizationError("receipt_missing")
            refresh_request.status = DemoRefreshRequest.Status.COMPLETED
            refresh_request.failure_code = ""
            notice_code = "demo_refresh_completed"
        else:
            bounded_code = failure_code or "orchestrator_failed"
            if not FAILURE_CODE_PATTERN.fullmatch(bounded_code):
                raise DemoRefreshFinalizationError("failure_code_invalid")
            refresh_request.status = DemoRefreshRequest.Status.FAILED
            refresh_request.failure_code = bounded_code
            notice_code = "demo_refresh_failed"

        refresh_request.save(
            update_fields=(
                "status",
                "finished_at",
                "receipt",
                "failure_code",
            )
        )
        _queue_request_notice(refresh_request, code=notice_code)
        return refresh_request

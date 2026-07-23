from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.dashboards.models import BackupExecution


FRESH_BACKUP_WINDOW = timedelta(hours=36)
RECENT_EXECUTIONS_LIMIT = 20


def continuity_snapshot(*, now=None, executions=None):
    now = now or timezone.now()
    if executions is None:
        executions = list(BackupExecution.objects.all()[:RECENT_EXECUTIONS_LIMIT])
    else:
        executions = list(executions)

    latest = executions[0] if executions else None
    latest_success = next(
        (run for run in executions if run.status == BackupExecution.Status.SUCCEEDED),
        None,
    )
    external_success = next(
        (
            run
            for run in executions
            if run.status == BackupExecution.Status.SUCCEEDED
            and run.destination == BackupExecution.Destination.EXTERNAL_ENCRYPTED
        ),
        None,
    )

    status = _status_payload(now=now, latest=latest, latest_success=latest_success)
    return {
        "status": status,
        "last_successful_at": _datetime_value(latest_success.finished_at if latest_success else None),
        "last_destination": latest_success.get_destination_display() if latest_success else None,
        "external_destination": {
            "configured": bool(external_success),
            "label": (
                "Registrado y verificado"
                if external_success
                else "No previsto en esta demo"
            ),
        },
        "schedule": _schedule_payload(
            now=now,
            latest=latest,
            latest_success=latest_success,
        ),
        "integrity_label": (
            "SHA-256 y HMAC verificados"
            if latest_success
            and latest_success.integrity_verified
            and latest_success.authenticity_verified
            else "Procedimiento disponible"
        ),
        "targets": {
            "rpo_hours": 24,
            "rto_hours": 2,
            "retention_label": "7 diarias · 4 semanales · 6 mensuales",
        },
        "history_url": reverse("dashboards:superadmin_continuity"),
        "recent_executions": [_execution_payload(run) for run in executions[:3]],
    }


def _schedule_payload(*, now, latest, latest_success):
    configured = settings.AGENDA_BACKUP_SCHEDULE_CONFIGURED
    if not configured:
        if latest_success:
            return {
                "configured": False,
                "code": "manual",
                "label": "Copias manuales",
                "detail": "Hay copias válidas, pero no existe una programación automática activa.",
            }
        return {
            "configured": False,
            "code": "stopped",
            "label": "Automatización detenida",
            "detail": "No hay una programación automática activa ni copias correctas registradas.",
        }
    if latest and latest.status == BackupExecution.Status.RUNNING:
        return {
            "configured": True,
            "code": "running",
            "label": "Copia automática en curso",
            "detail": "La tarea diaria está ejecutando una copia en este momento.",
        }
    if latest and latest.status == BackupExecution.Status.FAILED:
        return {
            "configured": True,
            "code": "attention",
            "label": "Automatización con incidencias",
            "detail": "La programación está configurada, pero la última ejecución falló.",
        }
    if latest_success:
        completed_at = latest_success.finished_at or latest_success.started_at
        if now - completed_at > FRESH_BACKUP_WINDOW:
            return {
                "configured": True,
                "code": "attention",
                "label": "Automatización necesita revisión",
                "detail": "La programación está configurada, pero no ha dejado una copia reciente.",
            }
        return {
            "configured": True,
            "code": "operational",
            "label": "Automatización diaria operativa",
            "detail": "La programación está activa y la última copia terminó correctamente.",
        }
    return {
        "configured": True,
        "code": "pending",
        "label": "Pendiente de primera copia",
        "detail": "La programación está configurada, pero todavía no hay una copia correcta.",
    }


def _status_payload(*, now, latest, latest_success):
    if latest and latest.status == BackupExecution.Status.FAILED:
        return {
            "code": "attention",
            "tone": "warning",
            "label": "Requiere revisión",
            "detail": "La última ejecución registrada no terminó correctamente.",
        }
    if latest and latest.status == BackupExecution.Status.RUNNING:
        return {
            "code": "running",
            "tone": "neutral",
            "label": "Copia en curso",
            "detail": "Hay una ejecución técnica abierta en este momento.",
        }
    if latest_success:
        completed_at = latest_success.finished_at or latest_success.started_at
        if now - completed_at > FRESH_BACKUP_WINDOW:
            return {
                "code": "stale",
                "tone": "warning",
                "label": "Copia desactualizada",
                "detail": "La última copia correcta supera el margen operativo de 36 horas.",
            }
        if latest_success.destination == BackupExecution.Destination.EXTERNAL_ENCRYPTED:
            return {
                "code": "protected",
                "tone": "ready",
                "label": "Continuidad protegida",
                "detail": "La última copia externa quedó autenticada y verificada.",
            }
        return {
            "code": "verified_local",
            "tone": "neutral",
            "label": "Copia local verificada",
            "detail": "La copia es válida y está conservada dentro del servidor de la demo.",
        }
    return {
        "code": "deployment_pending",
        "tone": "neutral",
        "label": "Preparado para desplegar",
        "detail": (
            "El procedimiento está preparado, pero todavía no hay una copia "
            "correcta registrada en este entorno."
        ),
    }


def _execution_payload(run):
    return {
        "id": run.id,
        "status": run.status,
        "status_label": run.get_status_display(),
        "destination": run.destination,
        "destination_label": run.get_destination_display(),
        "started_at": _datetime_value(run.started_at),
        "finished_at": _datetime_value(run.finished_at),
        "integrity_verified": run.integrity_verified,
        "authenticity_verified": run.authenticity_verified,
        "total_size_bytes": run.total_size_bytes,
        "failure_code": run.failure_code,
    }


def _datetime_value(value):
    return timezone.localtime(value) if value else None

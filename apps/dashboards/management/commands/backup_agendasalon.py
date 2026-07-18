from __future__ import annotations

import os
from pathlib import Path
import subprocess

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse
from django.utils import timezone

from apps.dashboards.models import BackupExecution
from apps.notifications.services import queue_operational_notice_on_commit
from ops.backup_restore import (
    DATABASE_DUMP_NAME,
    MANIFEST_NAME,
    MEDIA_ARCHIVE_NAME,
    BackupError,
    create_backup,
    verify_backup,
)


class Command(BaseCommand):
    help = "Crea y verifica una copia de PostgreSQL y media, y registra su resultado técnico."

    def add_arguments(self, parser):
        parser.add_argument("--backup-root", type=Path, required=True)
        parser.add_argument("--media-root", type=Path, default=Path(settings.MEDIA_ROOT))
        parser.add_argument("--pg-dump", default="pg_dump")
        parser.add_argument(
            "--destination",
            choices=[choice for choice, _label in BackupExecution.Destination.choices],
            default=BackupExecution.Destination.LOCAL,
            help=(
                "Declara dónde se escribe la copia. Usa external_encrypted únicamente "
                "si backup-root pertenece realmente a ese almacenamiento."
            ),
        )

    def handle(self, *args, **options):
        database_url = os.environ.get("DJANGO_DATABASE_URL", "").strip()
        integrity_key = os.environ.get("AGENDA_BACKUP_HMAC_KEY", "").strip()
        if not database_url:
            raise CommandError("DJANGO_DATABASE_URL es obligatoria para crear la copia.")
        if not integrity_key:
            raise CommandError("AGENDA_BACKUP_HMAC_KEY es obligatoria para autenticar la copia.")

        previous_status = (
            BackupExecution.objects.order_by("-started_at", "-pk")
            .values_list("status", flat=True)
            .first()
        )
        execution = BackupExecution.objects.create(
            status=BackupExecution.Status.RUNNING,
            destination=options["destination"],
        )
        try:
            backup_dir = create_backup(
                database_url=database_url,
                media_root=options["media_root"],
                backup_root=options["backup_root"],
                pg_dump_executable=options["pg_dump"],
                integrity_key=integrity_key,
            )
            verify_backup(
                backup_dir,
                integrity_key=integrity_key,
                require_authenticity=True,
            )
            total_size = sum(
                (backup_dir / filename).stat().st_size
                for filename in (DATABASE_DUMP_NAME, MEDIA_ARCHIVE_NAME, MANIFEST_NAME)
            )
        except (
            BackupError,
            ImproperlyConfigured,
            subprocess.CalledProcessError,
            OSError,
            ValueError,
        ) as exc:
            execution.status = BackupExecution.Status.FAILED
            execution.finished_at = timezone.now()
            execution.failure_code = _failure_code(exc)
            execution.save(update_fields=("status", "finished_at", "failure_code"))
            if previous_status != BackupExecution.Status.FAILED:
                queue_operational_notice_on_commit(
                    scope="platform",
                    code="continuity_failed",
                    deduplication_key=f"backup:{execution.pk}:failed",
                    action_path=reverse("dashboards:superadmin_continuity"),
                )
            raise CommandError(
                "La copia no se ha completado. Revisa el registro técnico de la ejecución."
            ) from exc

        execution.status = BackupExecution.Status.SUCCEEDED
        execution.finished_at = timezone.now()
        execution.database_included = True
        execution.media_included = True
        execution.integrity_verified = True
        execution.authenticity_verified = True
        execution.total_size_bytes = total_size
        execution.failure_code = ""
        execution.save(
            update_fields=(
                "status",
                "finished_at",
                "database_included",
                "media_included",
                "integrity_verified",
                "authenticity_verified",
                "total_size_bytes",
                "failure_code",
            )
        )
        if previous_status == BackupExecution.Status.FAILED:
            queue_operational_notice_on_commit(
                scope="platform",
                code="continuity_succeeded",
                deduplication_key=f"backup:{execution.pk}:succeeded",
                action_path=reverse("dashboards:superadmin_continuity"),
            )
        self.stdout.write(self.style.SUCCESS("Copia creada, autenticada y verificada."))


def _failure_code(exc):
    if isinstance(exc, subprocess.CalledProcessError):
        return "postgres_dump_failed"
    if isinstance(exc, ImproperlyConfigured):
        return "database_configuration_invalid"
    if isinstance(exc, BackupError):
        return "backup_validation_failed"
    if isinstance(exc, OSError):
        return "storage_operation_failed"
    return "backup_operation_failed"

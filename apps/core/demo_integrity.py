"""Guardas y comprobaciones de integridad para el refresco destructivo de la demo.

Este módulo mantiene separadas las comprobaciones del comando de gestión para
que puedan probarse sin fingir que SQLite es producción. La regeneración solo
se autoriza cuando el orquestador ha detenido todos los escritores, ha dejado
los medios en cuarentena y ha creado un marcador efímero firmado por un nonce.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from apps.core.demo_scenario import ACCESSES, DEMO_ADVISORY_LOCK_ID, DEMO_PASSWORD
from apps.core.models import DEMO_REFRESH_RUN_ID_PATTERN
from apps.holidays.services import (
    BOE_ADVISORY_LOCK_NAMESPACE,
    BOE_NATIONAL_SOURCE_NAME,
    BOE_TRANSACTION_ADVISORY_LOCK_ID,
)


DEMO_SEED_LOCK_ID = DEMO_ADVISORY_LOCK_ID
MARKER_MAX_AGE_SECONDS = 15 * 60
MARKER_REQUIRED_KEYS = frozenset(
    {"run_id", "created_at", "backup_dir", "media_quarantine", "media_root"}
)
CANONICAL_USER_PHONES = (
    "+34910000001",
    "+34600111001",
    "+34600222001",
)

ENV_REFRESH_ENABLED = "AGENDA_DEMO_REFRESH_ENABLED"
ENV_SUPPRESS_EMAIL = "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL"
ENV_RUN_ID = "AGENDA_DEMO_REFRESH_RUN_ID"
ENV_MARKER = "AGENDA_DEMO_QUIESCENCE_MARKER"
ENV_EXPECTED_DATABASE_NAME = "AGENDA_DEMO_EXPECTED_DATABASE_NAME"
ENV_EXPECTED_DATABASE_USER = "AGENDA_DEMO_EXPECTED_DATABASE_USER"
ENV_EXPECTED_DATABASE_HOST = "AGENDA_DEMO_EXPECTED_DATABASE_HOST"
ENV_EXPECTED_DATABASE_PORT = "AGENDA_DEMO_EXPECTED_DATABASE_PORT"
ENV_EXPECTED_WEBSITE = "AGENDA_DEMO_EXPECTED_PLATFORM_WEBSITE"
ENV_EXPECTED_MEDIA_ROOT = "AGENDA_DEMO_EXPECTED_MEDIA_ROOT"

_RUN_ID_RE = re.compile(DEMO_REFRESH_RUN_ID_PATTERN)


class DemoIntegrityError(RuntimeError):
    """La demo no cumple una condición necesaria para poder destruir datos."""


@dataclass(frozen=True)
class QuiescenceMarker:
    run_id: str
    created_at: float
    backup_dir: Path
    media_quarantine: Path
    media_root: Path


def _required_environment(environ, name: str) -> str:
    value = environ.get(name, "")
    if not value or value != value.strip():
        raise DemoIntegrityError(f"Falta {name} o contiene espacios exteriores.")
    return value


def validate_refresh_run_id(run_id: str) -> str:
    """Valida el identificador opaco compartido por el orquestador y el recibo."""

    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise DemoIntegrityError("El nonce del refresco no tiene un formato seguro.")
    return run_id


def _resolved_directory(
    raw_path: str,
    *,
    label: str,
    empty: bool = False,
    allow_logical_symlink: bool = False,
) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        raise DemoIntegrityError(f"{label} debe ser una ruta absoluta.")
    if path.is_symlink() and not allow_logical_symlink:
        raise DemoIntegrityError(f"{label} no puede ser un enlace simbólico.")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DemoIntegrityError(f"{label} no existe o no puede resolverse.") from exc
    if not resolved.is_dir():
        raise DemoIntegrityError(f"{label} debe ser un directorio real.")
    if empty:
        try:
            has_content = next(resolved.iterdir(), None) is not None
        except OSError as exc:
            raise DemoIntegrityError(f"No se puede inspeccionar {label}.") from exc
        if has_content:
            raise DemoIntegrityError(
                f"{label} debe estar vacío: el orquestador aún no ha aislado los medios."
            )
    return resolved


def _is_inside(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


class DemoRefreshGuard:
    """Preflight estricto e inmutable para el refresco nocturno."""

    def __init__(
        self,
        *,
        confirm_full_reset: bool,
        environ=None,
        settings_object=None,
        database_connection=None,
        now_epoch=None,
    ):
        self.confirm_full_reset = confirm_full_reset
        self.environ = os.environ if environ is None else environ
        self.settings = settings if settings_object is None else settings_object
        self.connection = connection if database_connection is None else database_connection
        self.now_epoch = time.time if now_epoch is None else now_epoch

    def validate(self) -> QuiescenceMarker:
        self._validate_static_contract()
        media_root = self._validate_media_root()
        marker = self._validate_marker(media_root)
        self._validate_database_identity()
        self._validate_database_role_and_connections()
        self._validate_migrations()
        self._validate_known_tables()
        return marker

    def _validate_static_contract(self) -> None:
        if not self.confirm_full_reset:
            raise DemoIntegrityError("Falta --confirm-full-reset.")
        if self.environ.get(ENV_REFRESH_ENABLED) != "1":
            raise DemoIntegrityError(f"{ENV_REFRESH_ENABLED} debe valer exactamente 1.")
        if self.environ.get(ENV_SUPPRESS_EMAIL) != "1":
            raise DemoIntegrityError(f"{ENV_SUPPRESS_EMAIL} debe valer exactamente 1.")
        if self.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings.prod":
            raise DemoIntegrityError(
                "El refresco solo admite DJANGO_SETTINGS_MODULE=config.settings.prod."
            )
        if self.settings.DEBUG is not False:
            raise DemoIntegrityError("DEBUG debe estar desactivado.")
        if not self.settings.AGENDA_PLATFORM_LEGAL_DEMO:
            raise DemoIntegrityError("La identidad legal debe estar en modo demo académico.")
        if not self.settings.AGENDA_BACKUP_SCHEDULE_CONFIGURED:
            raise DemoIntegrityError("La copia operativa programada debe estar configurada.")
        if self.settings.AGENDA_TRANSACTIONAL_EMAIL_ENABLED is not False:
            raise DemoIntegrityError("El correo transaccional debe estar desactivado en el proceso.")
        if self.settings.AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL is not True:
            raise DemoIntegrityError("La supresión interna de correo no está activa.")
        if self.settings.EMAIL_BACKEND != "django.core.mail.backends.dummy.EmailBackend":
            raise DemoIntegrityError("El proceso debe usar el backend de correo dummy.")
        for setting_name in (
            "SECURE_SSL_REDIRECT",
            "SESSION_COOKIE_SECURE",
            "CSRF_COOKIE_SECURE",
        ):
            if getattr(self.settings, setting_name, False) is not True:
                raise DemoIntegrityError(f"{setting_name} debe estar activado.")
        if self.connection.vendor != "postgresql":
            raise DemoIntegrityError("El refresco destructivo solo admite PostgreSQL.")

        expected_website = _required_environment(self.environ, ENV_EXPECTED_WEBSITE)
        if self.settings.AGENDA_PLATFORM_WEBSITE != expected_website:
            raise DemoIntegrityError("La URL pública no coincide con el identificador esperado.")
        parsed_website = urlparse(expected_website)
        if (
            parsed_website.scheme != "https"
            or not parsed_website.hostname
            or parsed_website.username
            or parsed_website.password
            or parsed_website.port not in (None, 443)
            or parsed_website.path not in ("", "/")
            or parsed_website.query
            or parsed_website.fragment
        ):
            raise DemoIntegrityError("La URL pública esperada debe ser HTTPS y tener host.")
        if "*" in set(self.settings.ALLOWED_HOSTS):
            raise DemoIntegrityError("ALLOWED_HOSTS no puede contener comodines.")
        if parsed_website.hostname not in set(self.settings.ALLOWED_HOSTS):
            raise DemoIntegrityError("El host público esperado no figura en ALLOWED_HOSTS.")
        expected_origin = f"https://{parsed_website.netloc}"
        if expected_origin not in set(self.settings.CSRF_TRUSTED_ORIGINS):
            raise DemoIntegrityError("El origen público esperado no figura en CSRF_TRUSTED_ORIGINS.")

    def _validate_media_root(self) -> Path:
        expected_raw = _required_environment(self.environ, ENV_EXPECTED_MEDIA_ROOT)
        actual = _resolved_directory(
            str(self.settings.MEDIA_ROOT),
            label="MEDIA_ROOT",
            empty=True,
            allow_logical_symlink=True,
        )
        expected = _resolved_directory(expected_raw, label=ENV_EXPECTED_MEDIA_ROOT, empty=True)
        if actual != expected:
            raise DemoIntegrityError("MEDIA_ROOT no coincide con la ruta esperada.")
        return actual

    def _validate_marker(self, media_root: Path) -> QuiescenceMarker:
        marker_raw = _required_environment(self.environ, ENV_MARKER)
        run_id = validate_refresh_run_id(_required_environment(self.environ, ENV_RUN_ID))

        marker_path = Path(marker_raw)
        if not marker_path.is_absolute():
            raise DemoIntegrityError("El marcador de quiescencia debe usar una ruta absoluta.")
        if marker_path.is_symlink():
            raise DemoIntegrityError("El marcador de quiescencia no puede ser un enlace simbólico.")
        try:
            marker_stat = marker_path.stat()
        except OSError as exc:
            raise DemoIntegrityError("No existe el marcador de quiescencia.") from exc
        if not stat.S_ISREG(marker_stat.st_mode):
            raise DemoIntegrityError("El marcador de quiescencia debe ser un archivo regular.")
        if hasattr(os, "geteuid"):
            if marker_stat.st_uid != 0:
                raise DemoIntegrityError("El marcador de quiescencia debe pertenecer a root.")
            if stat.S_IMODE(marker_stat.st_mode) & 0o022:
                raise DemoIntegrityError(
                    "El marcador de quiescencia no puede ser escribible por grupo u otros."
                )

        try:
            marker_text = marker_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DemoIntegrityError("No se puede leer el marcador de quiescencia.") from exc
        values = {}
        for raw_line in marker_text.splitlines():
            if not raw_line or "=" not in raw_line:
                raise DemoIntegrityError("El marcador de quiescencia tiene una línea inválida.")
            key, value = raw_line.split("=", 1)
            if key in values or not key or not value or value != value.strip():
                raise DemoIntegrityError("El marcador de quiescencia contiene datos ambiguos.")
            values[key] = value
        if set(values) != MARKER_REQUIRED_KEYS:
            raise DemoIntegrityError("El marcador de quiescencia no tiene el contrato exacto.")
        if values["run_id"] != run_id:
            raise DemoIntegrityError("El nonce del marcador no coincide con el de la ejecución.")

        try:
            created_at = float(values["created_at"])
        except ValueError as exc:
            raise DemoIntegrityError("created_at del marcador no es un epoch válido.") from exc
        if not math.isfinite(created_at):
            raise DemoIntegrityError("created_at del marcador no es un epoch finito.")
        age = self.now_epoch() - created_at
        if age < -30 or age > MARKER_MAX_AGE_SECONDS:
            raise DemoIntegrityError("El marcador de quiescencia está caducado o viene del futuro.")

        marker_media_root = _resolved_directory(values["media_root"], label="marker media_root")
        if marker_media_root != media_root:
            raise DemoIntegrityError("El MEDIA_ROOT del marcador no coincide.")
        backup_dir = _resolved_directory(values["backup_dir"], label="backup_dir")
        quarantine = _resolved_directory(
            values["media_quarantine"],
            label="media_quarantine",
        )
        if quarantine == media_root or _is_inside(quarantine, media_root):
            raise DemoIntegrityError("La cuarentena debe quedar fuera de MEDIA_ROOT.")
        if backup_dir == media_root or _is_inside(backup_dir, media_root):
            raise DemoIntegrityError("La copia verificada debe quedar fuera de MEDIA_ROOT.")

        return QuiescenceMarker(
            run_id=run_id,
            created_at=created_at,
            backup_dir=backup_dir,
            media_quarantine=quarantine,
            media_root=media_root,
        )

    def _validate_database_identity(self) -> None:
        expected_name = _required_environment(self.environ, ENV_EXPECTED_DATABASE_NAME)
        expected_user = _required_environment(self.environ, ENV_EXPECTED_DATABASE_USER)
        expected_host = _required_environment(self.environ, ENV_EXPECTED_DATABASE_HOST)
        expected_port_raw = _required_environment(
            self.environ,
            ENV_EXPECTED_DATABASE_PORT,
        )
        try:
            expected_port = int(expected_port_raw)
        except ValueError as exc:
            raise DemoIntegrityError("El puerto PostgreSQL esperado no es un entero.") from exc
        if not 1 <= expected_port <= 65_535 or str(expected_port) != expected_port_raw:
            raise DemoIntegrityError("El puerto PostgreSQL esperado no es canónico.")
        configured_name = str(self.connection.settings_dict.get("NAME", ""))
        configured_user = str(self.connection.settings_dict.get("USER", ""))
        configured_host = str(self.connection.settings_dict.get("HOST", ""))
        configured_port_raw = str(self.connection.settings_dict.get("PORT", "") or "5432")
        try:
            configured_port = int(configured_port_raw)
        except ValueError as exc:
            raise DemoIntegrityError("El puerto PostgreSQL configurado no es válido.") from exc
        if configured_name != expected_name:
            raise DemoIntegrityError("La base configurada no coincide con la esperada.")
        if configured_user != expected_user:
            raise DemoIntegrityError("El usuario PostgreSQL configurado no coincide con el esperado.")
        if configured_host != expected_host:
            raise DemoIntegrityError("El host PostgreSQL no coincide con el esperado.")
        if configured_port != expected_port:
            raise DemoIntegrityError("El puerto PostgreSQL no coincide con el esperado.")

        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, inet_server_port()"
            )
            actual_name, actual_user, actual_port = cursor.fetchone()
        if actual_name != expected_name:
            raise DemoIntegrityError("La conexión activa apunta a otra base de datos.")
        if actual_user != expected_user:
            raise DemoIntegrityError("La conexión activa usa otro rol PostgreSQL.")
        if actual_port != expected_port:
            raise DemoIntegrityError("La conexión activa usa otro puerto PostgreSQL.")

    def _validate_database_role_and_connections(self) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT current_user, current_schema(), pg_is_in_recovery(),
                       current_setting('transaction_read_only'),
                       rolsuper, rolcreatedb, rolcreaterole,
                       rolreplication, rolbypassrls
                  FROM pg_roles
                 WHERE rolname = current_user
                """
            )
            role_row = cursor.fetchone()
            if not role_row:
                raise DemoIntegrityError("No se pudo verificar el rol PostgreSQL.")
            if role_row[1] != "public":
                raise DemoIntegrityError("El esquema PostgreSQL activo debe ser public.")
            if role_row[2] is not False or role_row[3] != "off":
                raise DemoIntegrityError("PostgreSQL debe ser primario y admitir escrituras.")
            if any(role_row[4:]):
                raise DemoIntegrityError("El rol PostgreSQL no puede tener privilegios globales.")
        validate_no_other_client_connections(database_connection=self.connection)

    def _validate_migrations(self) -> None:
        executor = MigrationExecutor(self.connection)
        executor.loader.check_consistent_history(self.connection)
        targets = executor.loader.graph.leaf_nodes()
        if executor.migration_plan(targets):
            raise DemoIntegrityError("Hay migraciones pendientes de aplicar.")
        unknown_applied = set(executor.loader.applied_migrations) - set(
            executor.loader.disk_migrations
        )
        if unknown_applied:
            raise DemoIntegrityError("La base registra migraciones que el código no conoce.")

    def _validate_known_tables(self) -> None:
        expected = expected_database_tables()
        actual = set(self.connection.introspection.table_names())
        if actual != expected:
            missing = sorted(expected - actual)
            unknown = sorted(actual - expected)
            raise DemoIntegrityError(
                "El conjunto de tablas no coincide con el contrato conocido "
                f"(faltan={missing}, desconocidas={unknown})."
            )


def expected_database_tables() -> set[str]:
    """Tablas gestionadas por los modelos instalados, incluidas M2M y migraciones."""

    tables = {"django_migrations"}
    for model in apps.get_models(include_auto_created=True):
        if model._meta.managed and not model._meta.proxy:
            tables.add(model._meta.db_table)
    return tables


RESET_MODEL_ORDER = (
    "admin.LogEntry",
    "sessions.Session",
    "core.SecurityThrottle",
    "notifications.OutboundEmail",
    "notifications.InternalNotification",
    "businesses.PlatformActivityEvent",
    "businesses.BusinessActivityEvent",
    "holidays.HolidayAppointmentReview",
    "legal.CustomerPrivacyEvidenceEvent",
    "legal.LegalAcceptanceEvent",
    "legal.DataRightsRequest",
    "legal.CustomerPrivacyEvidence",
    "legal.LegalAcceptance",
    "booking.AppointmentService",
    "booking.Appointment",
    "booking.BusinessClosure",
    "booking.AvailabilityRule",
    "booking.BusinessCalendarSettings",
    "customers.BusinessClientAccessInvitation",
    "customers.BusinessClientAccessGrant",
    "customers.BusinessClientAuthorizedContact",
    "customers.BusinessClientAccess",
    "customers.BusinessClient",
    "booking.Service",
    "booking.WorkLine",
    "legal.BusinessLegalProfile",
    "businesses.BusinessPublicImage",
    "businesses.BusinessMembership",
    "businesses.BusinessSignupRequest",
    "businesses.PlatformLoginImage",
    "businesses.PlatformSettings",
    "businesses.Business",
    "auth.Group",
)

PRESERVED_MODEL_LABELS = frozenset(
    {
        "accounts.User",
        "auth.Permission",
        "contenttypes.ContentType",
        "core.DemoRefreshReceipt",
        "core.DemoRefreshRequest",
        "dashboards.BackupExecution",
        "holidays.HolidaySyncRun",
        "holidays.OfficialHoliday",
        "legal.LegalDocument",
    }
)

RESET_AUTO_CREATED_MODEL_LABELS = frozenset(
    {
        "auth.Group_permissions",
        "accounts.User_groups",
        "accounts.User_user_permissions",
    }
)


def assert_model_reset_contract() -> None:
    """Falla si se añade un modelo y nadie decide si se borra o se conserva."""

    installed = {
        model._meta.label
        for model in apps.get_models()
        if model._meta.managed and not model._meta.proxy
    }
    classified = set(RESET_MODEL_ORDER) | set(PRESERVED_MODEL_LABELS)
    if installed != classified:
        raise DemoIntegrityError(
            "El allowlist de modelos está desactualizado "
            f"(sin clasificar={sorted(installed - classified)}, "
            f"ausentes={sorted(classified - installed)})."
        )
    auto_created = {
        model._meta.label
        for model in apps.get_models(include_auto_created=True)
        if model._meta.auto_created
    }
    if auto_created != RESET_AUTO_CREATED_MODEL_LABELS:
        raise DemoIntegrityError(
            "El allowlist de tablas M2M está desactualizado "
            f"(sin clasificar={sorted(auto_created - RESET_AUTO_CREATED_MODEL_LABELS)}, "
            f"ausentes={sorted(RESET_AUTO_CREATED_MODEL_LABELS - auto_created)})."
        )


def required_boe_years(anchor_date: date, *, reference_date: date | None = None) -> tuple[int, ...]:
    """Años BOE que puede atravesar la ventana cronológica del escenario."""

    effective_reference = reference_date or anchor_date
    history_anchor = min(anchor_date, effective_reference)
    window_start = history_anchor - timedelta(days=60)
    window_end = max(anchor_date, effective_reference) + timedelta(days=40)
    return tuple(range(window_start.year, window_end.year + 1))


_BOE_REFERENCE_RE = re.compile(r"^BOE-A-[0-9]{4}-[0-9]+$")
_BOE_HOLIDAY_SIGNATURE_FIELDS = (
    "date",
    "name",
    "scope",
    "year",
    "source_name",
    "source_url",
    "official_reference",
    "loaded_at",
    "updated_at",
)
_BOE_RUN_SIGNATURE_FIELDS = (
    "year",
    "source_name",
    "source_url",
    "official_reference",
    "status",
    "started_at",
    "finished_at",
    "items_loaded",
    "items_created",
    "items_updated",
    "items_removed",
    "items_skipped",
    "affected_appointments",
    "affected_businesses",
    "error_detail",
    "created_by_id",
)


def _boe_expected_url(official_reference: str) -> str:
    return f"https://www.boe.es/diario_boe/txt.php?id={official_reference}"


def _traceable_boe_holidays_for_run(run):
    """Devuelve el catálogo real ligado a un SUCCESS, o ``None`` si no es íntegro."""

    from apps.holidays.models import OfficialHoliday

    if (
        run.source_name != BOE_NATIONAL_SOURCE_NAME
        or run.status != run.Status.SUCCESS
        or run.finished_at is None
        or run.finished_at < run.started_at
        or not _BOE_REFERENCE_RE.fullmatch(run.official_reference)
        or run.source_url != _boe_expected_url(run.official_reference)
        or run.error_detail
    ):
        return None
    holidays = tuple(
        OfficialHoliday._base_manager.filter(
            year=run.year,
            scope=OfficialHoliday.Scope.NATIONAL,
            source_name=BOE_NATIONAL_SOURCE_NAME,
            source_url=run.source_url,
            official_reference=run.official_reference,
        ).order_by("date", "pk")
    )
    if not 5 <= len(holidays) <= 14 or run.items_loaded != len(holidays):
        return None
    if run.items_created + run.items_updated + run.items_skipped != run.items_loaded:
        return None
    if any(holiday.date.year != run.year or not holiday.name.strip() for holiday in holidays):
        return None
    return holidays


def _latest_valid_boe_catalog(year: int):
    """Selecciona el SUCCESS BOE más reciente que conserva su catálogo completo."""

    from apps.holidays.models import HolidaySyncRun

    candidates = HolidaySyncRun._base_manager.filter(
        year=year,
        source_name=BOE_NATIONAL_SOURCE_NAME,
        status=HolidaySyncRun.Status.SUCCESS,
        finished_at__isnull=False,
    ).order_by("-finished_at", "-pk")
    for run in candidates:
        holidays = _traceable_boe_holidays_for_run(run)
        if holidays is not None:
            return run, holidays
    raise DemoIntegrityError(f"Falta una sincronización BOE correcta para {year}.")


def _boe_records_signature(holidays, runs, *, neutralize_created_by: bool = False) -> str:
    ordered_holidays = sorted(holidays, key=lambda item: (item.date, item.scope, item.pk))
    ordered_runs = sorted(runs, key=lambda item: (item.started_at, item.pk))
    payload = {
        "holidays": [
            tuple(getattr(holiday, field) for field in _BOE_HOLIDAY_SIGNATURE_FIELDS)
            for holiday in ordered_holidays
        ],
        "runs": [
            tuple(
                None
                if neutralize_created_by and field == "created_by_id"
                else getattr(run, field)
                for field in _BOE_RUN_SIGNATURE_FIELDS
            )
            for run in ordered_runs
        ],
    }
    return _fingerprint(payload)


def canonicalize_boe_catalog(
    anchor_date: date,
    *,
    reference_date: date | None = None,
) -> tuple[int, ...]:
    """Conserva la última foto BOE íntegra de cada año real disponible.

    No reconstruye ni descarga datos. Antes de borrar identifica filas reales
    cuya referencia, URL y resumen coinciden; después conserva exactamente esas
    filas, elimina ejecuciones parciales o ajenas y neutraliza su actor mutable.
    """

    from apps.holidays.models import HolidaySyncRun, OfficialHoliday

    if not transaction.get_connection().in_atomic_block:
        raise DemoIntegrityError("La canonicalización BOE requiere una transacción.")
    required_years = required_boe_years(anchor_date, reference_date=reference_date)
    candidate_years = tuple(
        HolidaySyncRun._base_manager.filter(
            source_name=BOE_NATIONAL_SOURCE_NAME,
            status=HolidaySyncRun.Status.SUCCESS,
            finished_at__isnull=False,
        )
        .order_by("year")
        .values_list("year", flat=True)
        .distinct()
    )
    selected = []
    for year in candidate_years:
        try:
            selected.append(_latest_valid_boe_catalog(year))
        except DemoIntegrityError:
            # Un año compuesto solo por ejecuciones o catálogos incoherentes
            # no es una fuente real disponible y se elimina como residuo.
            continue
    canonical_years = tuple(run.year for run, _ in selected)
    missing_required = sorted(set(required_years) - set(canonical_years))
    if missing_required:
        raise DemoIntegrityError(
            "Falta una sincronización BOE correcta para los años requeridos "
            f"{missing_required}."
        )
    selected_runs = tuple(run for run, _ in selected)
    selected_holidays = tuple(
        holiday for _, holidays in selected for holiday in holidays
    )
    expected_signature = _boe_records_signature(
        selected_holidays,
        selected_runs,
        neutralize_created_by=True,
    )
    kept_run_ids = [run.pk for run in selected_runs]
    kept_holiday_ids = [holiday.pk for holiday in selected_holidays]

    OfficialHoliday._base_manager.exclude(pk__in=kept_holiday_ids).delete()
    HolidaySyncRun._base_manager.exclude(pk__in=kept_run_ids).delete()
    HolidaySyncRun._base_manager.filter(pk__in=kept_run_ids).exclude(
        created_by__isnull=True
    ).update(created_by=None)

    validate_boe_coverage(anchor_date, reference_date=reference_date)
    if boe_signature() != expected_signature:
        raise DemoIntegrityError("La firma BOE no coincide tras canonicalizar el catálogo.")
    return canonical_years


def validate_boe_coverage(anchor_date: date, *, reference_date: date | None = None) -> tuple[int, ...]:
    """Exige la foto BOE canónica exacta, sin residuos ni consultas a Internet."""

    from apps.holidays.models import HolidaySyncRun, OfficialHoliday

    years = required_boe_years(anchor_date, reference_date=reference_date)
    all_runs = tuple(HolidaySyncRun._base_manager.order_by("year", "pk"))
    canonical_years = {run.year for run in all_runs}
    if not set(years).issubset(canonical_years) or len(all_runs) != len(canonical_years):
        raise DemoIntegrityError("Las ejecuciones BOE conservan años o residuos no canónicos.")
    canonical_holiday_ids = set()
    for year in sorted(canonical_years):
        runs = [run for run in all_runs if run.year == year]
        if len(runs) != 1 or runs[0].created_by_id is not None:
            raise DemoIntegrityError(f"La ejecución BOE canónica de {year} es ambigua.")
        holidays = _traceable_boe_holidays_for_run(runs[0])
        if holidays is None:
            raise DemoIntegrityError(f"La cobertura BOE de {year} está incompleta.")
        canonical_holiday_ids.update(holiday.pk for holiday in holidays)
    all_holiday_ids = set(
        OfficialHoliday._base_manager.values_list("pk", flat=True)
    )
    if all_holiday_ids != canonical_holiday_ids:
        raise DemoIntegrityError("El catálogo BOE conserva festivos no canónicos.")
    return years


def validate_no_other_client_connections(*, database_connection=None) -> None:
    """Comprueba que no hay otro cliente conectado a la base actual."""

    active_connection = database_connection or connection
    try:
        with active_connection.cursor() as cursor:
            # PostgreSQL puede conservar una foto de estadísticas durante toda
            # la transacción. La invalidamos para que la segunda barrera vea
            # conexiones aparecidas después del primer preflight.
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                """
                SELECT COUNT(*)
                  FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND pid <> pg_backend_pid()
                   AND backend_type = 'client backend'
                """
            )
            other_connections = cursor.fetchone()[0]
    except DatabaseError as exc:
        raise DemoIntegrityError(
            "No se pudo revalidar la quiescencia de PostgreSQL."
        ) from exc
    if other_connections:
        raise DemoIntegrityError(
            "La base aún tiene otras conexiones cliente; no existe quiescencia real."
        )


def application_database_tables() -> tuple[str, ...]:
    """Tablas ORM cuyo estado puede leer, preservar o regenerar el refresco."""

    return tuple(sorted(expected_database_tables() - {"django_migrations"}))


def acquire_application_table_locks() -> None:
    """Bloquea sin espera todas las tablas ORM durante la foto canónica.

    Un cliente externo no cooperativo que se conecte después de la última
    revalidación no puede escribir hasta el commit. Una escritura que empiece
    después del commit ya pertenece, de forma inevitable, al siguiente periodo
    de uso de la demo.
    """

    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise DemoIntegrityError("Los bloqueos de tablas requieren PostgreSQL y atomic().")
    try:
        with connection.cursor() as cursor:
            for table_name in application_database_tables():
                quoted_table = connection.ops.quote_name(table_name)
                cursor.execute(
                    f"LOCK TABLE {quoted_table} IN ACCESS EXCLUSIVE MODE NOWAIT"
                )
    except DatabaseError as exc:
        raise DemoIntegrityError(
            "Otra operación usa una tabla de la aplicación; se cancela el refresco."
        ) from exc


def acquire_refresh_locks(*, boe_years: tuple[int, ...] = ()) -> None:
    """Adquiere mutex lógicos y bloqueos exclusivos sin esperar."""

    if connection.vendor != "postgresql" or not connection.in_atomic_block:
        raise DemoIntegrityError("Los bloqueos del refresco requieren PostgreSQL y atomic().")
    lock_specs = [("single", DEMO_SEED_LOCK_ID, "regeneración demo")]
    lock_specs.extend(
        ("pair", year, f"sincronización BOE {year}") for year in sorted(set(boe_years))
    )
    lock_specs.append(("single", BOE_TRANSACTION_ADVISORY_LOCK_ID, "reconciliación BOE"))
    for lock_kind, lock_id, label in lock_specs:
        with connection.cursor() as cursor:
            if lock_kind == "pair":
                cursor.execute(
                    "SELECT pg_try_advisory_xact_lock(%s, %s)",
                    [BOE_ADVISORY_LOCK_NAMESPACE, lock_id],
                )
            else:
                cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [lock_id])
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise DemoIntegrityError(f"Ya hay otra {label} en curso.")
    acquire_application_table_locks()
    validate_no_other_client_connections()


def delete_mutable_demo_data() -> dict[str, int]:
    """Vacía el allowlist mutable y conserva las tres identidades internas canónicas."""

    if not transaction.get_connection().in_atomic_block:
        raise DemoIntegrityError("La limpieza debe ejecutarse dentro de una transacción.")
    assert_model_reset_contract()
    deleted = {}
    for label in RESET_MODEL_ORDER:
        model = apps.get_model(label)
        count, _ = model._base_manager.all().delete()
        deleted[label] = count

    auto_created_models = {
        model._meta.label: model
        for model in apps.get_models(include_auto_created=True)
        if model._meta.auto_created
    }
    for label in sorted(RESET_AUTO_CREATED_MODEL_LABELS):
        count, _ = auto_created_models[label]._base_manager.all().delete()
        deleted[label] = count

    User = get_user_model()
    count, _ = User._base_manager.exclude(
        normalized_phone__in=CANONICAL_USER_PHONES
    ).delete()
    deleted["accounts.User(noncanonical)"] = count
    return deleted


RESIDUAL_MODEL_LABELS = (
    "admin.LogEntry",
    "sessions.Session",
    "core.SecurityThrottle",
    "notifications.OutboundEmail",
    "businesses.PlatformActivityEvent",
    "businesses.BusinessSignupRequest",
    "businesses.BusinessPublicImage",
    "businesses.PlatformLoginImage",
    "customers.BusinessClientAccessInvitation",
    "holidays.HolidayAppointmentReview",
    "legal.DataRightsRequest",
    "auth.Group",
)


def evaluator_residue_counts() -> dict[str, int]:
    """Estado que una regeneración canónica nunca debe conservar o recrear."""

    counts = {
        label: apps.get_model(label)._base_manager.count() for label in RESIDUAL_MODEL_LABELS
    }
    User = get_user_model()
    counts["accounts.User.groups"] = User.groups.through._base_manager.count()
    counts["accounts.User.user_permissions"] = (
        User.user_permissions.through._base_manager.count()
    )
    counts["accounts.User(noncanonical)"] = User._base_manager.exclude(
        normalized_phone__in=CANONICAL_USER_PHONES
    ).count()
    return counts


def assert_no_evaluator_residue() -> None:
    residues = {label: count for label, count in evaluator_residue_counts().items() if count}
    if residues:
        raise DemoIntegrityError(f"Persisten residuos ajenos al escenario canónico: {residues}.")


def _json_default(value):
    if isinstance(value, (date, datetime, datetime_time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Tipo no serializable en firma: {type(value)!r}")


def _fingerprint(payload) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def protected_records_signature() -> str:
    """Firma de los registros globales que el refresco no puede alterar."""

    from apps.core.models import DemoRefreshReceipt, DemoRefreshRequest
    from apps.dashboards.models import BackupExecution
    from apps.holidays.models import HolidaySyncRun, OfficialHoliday
    from apps.legal.models import LegalDocument

    payload = {
        "demo_refresh_receipts": list(
            DemoRefreshReceipt._base_manager.order_by("pk").values_list(
                "pk",
                "run_id",
                "base_date",
                "fingerprint",
                "completed_at",
            )
        ),
        "demo_refresh_requests": list(
            DemoRefreshRequest._base_manager.order_by("pk").values_list(
                "pk",
                "public_id",
                "requested_by_id",
                "base_date",
                "status",
                "requested_at",
                "started_at",
                "finished_at",
                "receipt_id",
                "failure_code",
                "origin_digest",
            )
        ),
        "legal_documents": list(
            LegalDocument._base_manager.order_by("pk").values_list(
                "pk",
                "kind",
                "slug",
                "version",
                "title",
                "lead",
                "sections",
                "content_hash",
                "published_at",
                "is_active",
            )
        ),
        "official_holidays": list(
            OfficialHoliday._base_manager.order_by("pk").values_list(
                "pk",
                "date",
                "name",
                "scope",
                "year",
                "source_name",
                "source_url",
                "official_reference",
                "loaded_at",
                "updated_at",
            )
        ),
        "holiday_sync_runs": list(
            HolidaySyncRun._base_manager.order_by("pk").values_list(
                "pk",
                "year",
                "source_name",
                "source_url",
                "official_reference",
                "status",
                "started_at",
                "finished_at",
                "items_loaded",
                "items_created",
                "items_updated",
                "items_removed",
                "items_skipped",
                "affected_appointments",
                "affected_businesses",
                "error_detail",
                "created_by_id",
            )
        ),
        "backup_executions": list(
            BackupExecution._base_manager.order_by("pk").values_list(
                "pk",
                "status",
                "destination",
                "started_at",
                "finished_at",
                "database_included",
                "media_included",
                "integrity_verified",
                "authenticity_verified",
                "total_size_bytes",
                "failure_code",
            )
        ),
    }
    return _fingerprint(payload)


def boe_signature() -> str:
    """Firma semántica del BOE, incluido el actor ya neutralizado."""

    from apps.holidays.models import HolidaySyncRun, OfficialHoliday

    return _boe_records_signature(
        tuple(OfficialHoliday._base_manager.all()),
        tuple(HolidaySyncRun._base_manager.all()),
    )


def _semantic_rows(queryset, *fields):
    return list(queryset.order_by(*fields).values_list(*fields))


def demo_semantic_fingerprint() -> str:
    """Huella estable del escenario, independiente de PK y hashes aleatorios."""

    from apps.booking.models import (
        Appointment,
        AppointmentService,
        AvailabilityRule,
        BusinessCalendarSettings,
        BusinessClosure,
        Service,
        WorkLine,
    )
    from apps.businesses.models import (
        Business,
        BusinessActivityEvent,
        BusinessMembership,
        PlatformSettings,
    )
    from apps.customers.models import (
        BusinessClient,
        BusinessClientAccess,
        BusinessClientAccessGrant,
        BusinessClientAuthorizedContact,
    )
    from apps.legal.models import (
        BusinessLegalProfile,
        CustomerPrivacyEvidence,
        CustomerPrivacyEvidenceEvent,
        LegalAcceptance,
        LegalAcceptanceEvent,
    )
    from apps.notifications.models import InternalNotification

    User = get_user_model()
    users = tuple(User.objects.order_by("normalized_phone"))
    accesses_by_email = {
        access.email_normalized: access
        for access in BusinessClientAccess.objects.select_related(
            "business",
            "business_client",
        )
    }
    payload = {
        "evaluator_residue": sorted(evaluator_residue_counts().items()),
        "users": _semantic_rows(
            User.objects,
            "normalized_phone",
            "full_name",
            "email",
            "is_staff",
            "is_superuser",
            "is_active",
            "password_change_required",
        ),
        "user_demo_passwords": [
            (user.normalized_phone, check_password(DEMO_PASSWORD, user.password)) for user in users
        ],
        "businesses": _semantic_rows(
            Business.objects,
            "slug",
            "commercial_name",
            "public_description",
            "public_phone",
            "public_email",
            "address",
            "city",
            "province",
            "is_active",
            "public_booking_enabled",
            "legal_compliance_enabled",
            "notification_email",
            "notification_email_normalized",
            "notification_email_verified_at",
            "notifications_enabled",
            "notify_new_appointments",
            "notify_cancellations",
            "notify_client_access",
            "notify_holiday_reviews",
            "notify_email_failures",
            "professional_theme",
            "public_image",
            "public_image_preset",
        ),
        "memberships": _semantic_rows(
            BusinessMembership.objects,
            "business__slug",
            "user__normalized_phone",
            "role",
            "is_active",
        ),
        "platform": _semantic_rows(
            PlatformSettings.objects,
            "admin_theme",
            "login_image_preset",
            "notification_email",
            "notification_email_normalized",
            "notification_email_verified_at",
            "notifications_enabled",
            "notify_continuity",
            "notify_demo_refresh",
            "notify_signup_requests",
            "notify_email_failures",
            "updated_by__normalized_phone",
        ),
        "calendars": _semantic_rows(
            BusinessCalendarSettings.objects,
            "business__slug",
            "slot_interval_minutes",
            "apply_national_holidays",
        ),
        "availability": _semantic_rows(
            AvailabilityRule.objects,
            "business__slug",
            "weekday",
            "start_time",
            "end_time",
            "is_active",
        ),
        "services": _semantic_rows(
            Service.objects,
            "business__slug",
            "display_order",
            "name",
            "description",
            "duration_minutes",
            "price_amount",
            "color_hex",
            "is_active",
        ),
        "work_lines": _semantic_rows(
            WorkLine.objects,
            "business__slug",
            "line_number",
            "name",
            "is_active",
            "display_order",
        ),
        "clients": _semantic_rows(
            BusinessClient.objects,
            "business__slug",
            "full_name_normalized",
            "full_name",
            "phone_normalized",
            "phone",
            "email",
            "source",
            "is_active",
            "internal_notes",
            "created_at",
            "last_activity_at",
        ),
        "accesses": _semantic_rows(
            BusinessClientAccess.objects,
            "business__slug",
            "business_client__full_name_normalized",
            "phone_normalized",
            "email_normalized",
            "email_verified_at",
            "is_active",
            "is_pending_public_registration",
            "public_registration_expires_at",
            "last_login_at",
            "created_at",
        ),
        "access_demo_passwords": [
            (
                definition.email.lower(),
                bool(
                    (access := accesses_by_email.get(definition.email.lower()))
                    and check_password(definition.password, access.password_hash)
                ),
            )
            for definition in ACCESSES
        ],
        "contacts": _semantic_rows(
            BusinessClientAuthorizedContact.objects,
            "business__slug",
            "business_client__full_name_normalized",
            "linked_business_client__full_name_normalized",
            "full_name",
            "phone_normalized",
            "relationship_label",
            "is_primary_contact",
            "notes",
            "is_active",
        ),
        "grants": _semantic_rows(
            BusinessClientAccessGrant.objects,
            "business__slug",
            "access__business_client__full_name_normalized",
            "business_client__full_name_normalized",
            "relationship_label",
            "is_active",
        ),
        "closures": _semantic_rows(
            BusinessClosure.objects,
            "business__slug",
            "date_from",
            "date_to",
            "work_line__line_number",
            "start_time",
            "end_time",
            "closure_type",
            "internal_reason",
            "is_active",
        ),
        "appointments": _semantic_rows(
            Appointment.objects,
            "business__slug",
            "starts_at",
            "work_line__line_number",
            "business_client__full_name_normalized",
            "ends_at",
            "total_duration_minutes",
            "duration_adjustment_reason",
            "status",
            "manual_channel",
            "created_by__normalized_phone",
            "requested_by_client_access__business_client__full_name_normalized",
            "requested_by_name_snapshot",
            "requested_by_relationship_snapshot",
            "public_confirmation_reference",
            "cancelled_by__normalized_phone",
            "cancelled_at",
            "cancellation_reason",
            "completed_by__normalized_phone",
            "completed_at",
            "no_show_marked_by__normalized_phone",
            "no_show_marked_at",
            "service_summary_snapshot",
            "created_at",
        ),
        "appointment_services": _semantic_rows(
            AppointmentService.objects,
            "appointment__business__slug",
            "appointment__starts_at",
            "appointment__work_line__line_number",
            "appointment__business_client__full_name_normalized",
            "display_order",
            "service_name_snapshot",
            "duration_minutes_snapshot",
            "price_amount_snapshot",
            "color_hex_snapshot",
        ),
        "notifications": _semantic_rows(
            InternalNotification.objects,
            "business__slug",
            "event_type",
            "channel",
            "content",
            "status",
            "created_at",
            "read_at",
        ),
        "activity": _semantic_rows(
            BusinessActivityEvent.objects,
            "business__slug",
            "created_at",
            "category",
            "event_type",
            "origin",
            "actor_type",
            "actor_label",
            "summary",
            "entity_type",
            "changes",
        ),
        "legal_profiles": _semantic_rows(
            BusinessLegalProfile.objects,
            "business__slug",
            "legal_name",
            "tax_identifier",
            "registered_address",
            "privacy_email",
            "rights_contact_name",
            "retention_criteria",
        ),
        "acceptances": _semantic_rows(
            LegalAcceptance.objects,
            "business__slug",
            "document__kind",
            "document__version",
            "actor_user__normalized_phone",
            "client_access__business_client__full_name_normalized",
            "action",
            "context",
            "document_hash_snapshot",
            "legal_context_snapshot",
            "authority_declared",
            "accepted_at",
        ),
        "acceptance_events": _semantic_rows(
            LegalAcceptanceEvent._base_manager,
            "business__slug",
            "document__kind",
            "document__version",
            "actor_user__normalized_phone",
            "client_access__business_client__full_name_normalized",
            "action",
            "context",
            "document_hash_snapshot",
            "legal_context_snapshot",
            "authority_declared",
            "accepted_at",
            "action_fingerprint",
        ),
        "privacy": _semantic_rows(
            CustomerPrivacyEvidence.objects,
            "business__slug",
            "business_client__full_name_normalized",
            "document__version",
            "event_type",
            "channel",
            "informed_party_type",
            "informed_party_name_snapshot",
            "document_hash_snapshot",
            "legal_context_snapshot",
            "occurred_at",
        ),
        "privacy_events": _semantic_rows(
            CustomerPrivacyEvidenceEvent._base_manager,
            "business__slug",
            "business_client__full_name_normalized",
            "document__version",
            "event_type",
            "channel",
            "informed_party_type",
            "informed_party_name_snapshot",
            "document_hash_snapshot",
            "legal_context_snapshot",
            "occurred_at",
            "action_fingerprint",
        ),
    }
    return _fingerprint(payload)

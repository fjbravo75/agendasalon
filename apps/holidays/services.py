from __future__ import annotations

import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from threading import Lock

import requests
from bs4 import BeautifulSoup
from django.db import transaction
from django.utils import timezone

from apps.holidays.models import HolidaySyncRun, OfficialHoliday


BOE_NATIONAL_SOURCE_NAME = "BOE - calendario laboral nacional"
BOE_ADVISORY_LOCK_NAMESPACE = 0x4147424F
BOE_TRANSACTION_ADVISORY_LOCK_ID = 0x4147424F5032
BOE_INTERRUPTED_RUN_ERROR = (
    "La sincronización anterior se interrumpió antes de terminar. "
    "No se aplicó un resultado completo."
)
BOE_NETWORK_ERROR = (
    "No hemos podido consultar el BOE. Comprueba la conexión y vuelve a intentarlo."
)
BOE_INTERNAL_RUN_ERROR = (
    "La sincronización se interrumpió por un error interno. "
    "No se aplicó un resultado completo."
)
_BOE_LOCAL_YEAR_LOCKS = {}
_BOE_LOCAL_YEAR_LOCKS_GUARD = Lock()


class BoeSyncError(Exception):
    """Expected BOE lookup, download or parsing failure."""


@dataclass(frozen=True, slots=True)
class BoeHolidayResolution:
    identifier: str
    title: str
    url_html: str


@dataclass(frozen=True, slots=True)
class OfficialHolidayImport:
    day: date
    name: str


@dataclass(frozen=True, slots=True)
class OfficialHolidaySyncResult:
    run: HolidaySyncRun
    resolution: BoeHolidayResolution


class BoeNationalHolidaySyncService:
    MAX_RESPONSE_BYTES = 2 * 1024 * 1024
    RESPONSE_CHUNK_BYTES = 64 * 1024
    SEARCH_URL = "https://www.boe.es/buscar/boe.php"
    DOCUMENT_URL = "https://www.boe.es/diario_boe/txt.php?id={identifier}"
    NATIONAL_MARKERS = {"*", "**"}
    MONTHS = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    }

    def __init__(self, *, session: requests.Session | None = None):
        self.session = session or requests.Session()

    def fetch_national_holidays(
        self,
        target_year: int,
    ) -> tuple[BoeHolidayResolution, tuple[OfficialHolidayImport, ...]]:
        resolution = self.find_resolution(target_year)
        resolution_html = self.fetch_resolution_html(resolution.url_html)
        holidays = self.extract_national_holidays(target_year, resolution_html)
        if not 5 <= len(holidays) <= 14:
            raise BoeSyncError(
                "La resolución se ha encontrado, pero el número de festivos nacionales "
                f"extraídos ({len(holidays)}) no es seguro para actualizar el calendario."
            )
        return resolution, holidays

    def find_resolution(self, target_year: int) -> BoeHolidayResolution:
        publication_year = target_year - 1
        expected_title = self._normalize_text(
            f"relación de fiestas laborales para el año {target_year}"
        )
        response_text = self._get_boe_text(
            self.SEARCH_URL,
            response_label="El buscador oficial del BOE",
            params={
                "campo[0]": "ORIS",
                "dato[0][1]": "1",
                "operador[0]": "and",
                "campo[1]": "TITULOS",
                "dato[1]": f"relación de fiestas laborales para el año {target_year}",
                "operador[1]": "and",
                "campo[6]": "FPU",
                "dato[6][0]": f"{publication_year}-01-01",
                "dato[6][1]": f"{publication_year}-12-31",
                "page_hits": "50",
                "sort_field[0]": "FPU",
                "sort_order[0]": "desc",
                "accion": "Buscar",
            },
        )

        soup = BeautifulSoup(response_text, "html.parser")
        for result in soup.select("li.resultado-busqueda"):
            title = ""
            for title_node in result.find_all("p"):
                candidate = self._clean_text(title_node.get_text(" ", strip=True))
                if expected_title in self._normalize_text(candidate):
                    title = candidate
                    break
            if not title:
                continue
            reference_link = result.find(
                "a",
                attrs={"title": re.compile(r"^Ref\. BOE-A-\d{4}-\d+$")},
            )
            if reference_link is None:
                continue
            identifier = self._clean_text(reference_link.get("title", "")).removeprefix(
                "Ref. "
            )
            return BoeHolidayResolution(
                identifier=identifier,
                title=title,
                url_html=self.DOCUMENT_URL.format(identifier=identifier),
            )

        raise BoeSyncError(
            f"No se ha encontrado en el BOE la resolución de fiestas laborales para {target_year}."
        )

    def fetch_resolution_html(self, url_html: str) -> str:
        if not url_html.startswith("https://www.boe.es/"):
            raise BoeSyncError("La URL de la resolución no pertenece al BOE permitido.")
        return self._get_boe_text(
            url_html,
            response_label="La resolución del BOE",
        )

    def _get_boe_text(self, url, *, response_label, **kwargs):
        try:
            response = self.session.get(
                url,
                timeout=(5, 20),
                allow_redirects=False,
                stream=True,
                **kwargs,
            )
            if 300 <= response.status_code < 400:
                raise BoeSyncError(
                    f"{response_label} ha intentado redirigir la descarga."
                )
            if response.status_code != 200:
                raise BoeSyncError(
                    f"{response_label} ha devuelto {response.status_code}."
                )

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > self.MAX_RESPONSE_BYTES:
                    raise BoeSyncError(
                        f"{response_label} supera el tamaño máximo permitido."
                    )

            body = bytearray()
            for chunk in response.iter_content(chunk_size=self.RESPONSE_CHUNK_BYTES):
                if not chunk:
                    continue
                body.extend(chunk)
                if len(body) > self.MAX_RESPONSE_BYTES:
                    raise BoeSyncError(
                        f"{response_label} supera el tamaño máximo permitido."
                    )
        except requests.RequestException as error:
            raise BoeSyncError(BOE_NETWORK_ERROR) from error
        encoding = response.encoding or "utf-8"
        return bytes(body).decode(encoding, errors="replace")

    def extract_national_holidays(
        self,
        target_year: int,
        resolution_html: str,
    ) -> tuple[OfficialHolidayImport, ...]:
        soup = BeautifulSoup(resolution_html, "html.parser")
        table = soup.find("table")
        if table is None:
            raise BoeSyncError(
                "La resolución del BOE no contiene una tabla de festivos reconocible."
            )

        current_month = None
        holidays_by_day = {}
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if not cells:
                continue

            first_cell = self._clean_text(cells[0].get_text(" ", strip=True))
            normalized_first_cell = self._normalize_text(first_cell.rstrip("."))
            if normalized_first_cell in self.MONTHS:
                current_month = self.MONTHS[normalized_first_cell]
                continue

            match = re.match(r"^(?P<day>\d{1,2})\s+(?P<name>.+)$", first_cell)
            if match is None or current_month is None:
                continue

            markers = [self._clean_text(cell.get_text(" ", strip=True)) for cell in cells[1:]]
            if markers and all(marker in self.NATIONAL_MARKERS for marker in markers):
                holiday = OfficialHolidayImport(
                    day=date(target_year, current_month, int(match.group("day"))),
                    name=match.group("name").rstrip(".").strip(),
                )
                holidays_by_day[holiday.day] = holiday

        return tuple(holidays_by_day[day] for day in sorted(holidays_by_day))

    @staticmethod
    def _clean_text(value: str) -> str:
        return " ".join(value.split())

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        return normalized.encode("ascii", "ignore").decode("ascii").lower().strip()


def sync_boe_national_holidays(
    target_year: int,
    *,
    created_by=None,
    service: BoeNationalHolidaySyncService | None = None,
) -> OfficialHolidaySyncResult:
    sync_service = service or BoeNationalHolidaySyncService()
    with _boe_year_sync_guard(target_year):
        _mark_interrupted_boe_runs(target_year, at=timezone.now())
        run = HolidaySyncRun.objects.create(
            year=target_year,
            source_name=BOE_NATIONAL_SOURCE_NAME,
            status=HolidaySyncRun.Status.FAILED,
            started_at=timezone.now(),
            created_by=created_by,
        )

        try:
            resolution, holidays = sync_service.fetch_national_holidays(target_year)
            with transaction.atomic():
                _lock_boe_reconciliation_transaction()
                locked_calendars = _lock_all_business_calendars()
                counts = _reconcile_national_holidays(
                    target_year,
                    resolution,
                    holidays,
                )
                affected_appointments, affected_businesses = (
                    _locked_affected_future_appointments(
                        holidays,
                        locked_calendars=locked_calendars,
                        at=timezone.now(),
                    )
                )

                run.source_url = resolution.url_html
                run.official_reference = resolution.identifier
                run.status = HolidaySyncRun.Status.SUCCESS
                run.finished_at = timezone.now()
                run.items_loaded = len(holidays)
                run.items_created = counts["created"]
                run.items_updated = counts["updated"]
                run.items_removed = counts["removed"]
                run.items_skipped = counts["skipped"]
                run.affected_appointments = affected_appointments
                run.affected_businesses = affected_businesses
                run.error_detail = ""
                run.save(
                    update_fields=[
                        "source_url",
                        "official_reference",
                        "status",
                        "finished_at",
                        "items_loaded",
                        "items_created",
                        "items_updated",
                        "items_removed",
                        "items_skipped",
                        "affected_appointments",
                        "affected_businesses",
                        "error_detail",
                    ]
                )
        except requests.RequestException as error:
            _finish_failed_boe_run(run, error_detail=BOE_NETWORK_ERROR)
            raise BoeSyncError(BOE_NETWORK_ERROR) from error
        except BoeSyncError as error:
            _finish_failed_boe_run(run, error_detail=str(error))
            raise
        except Exception:
            _finish_failed_boe_run(run, error_detail=BOE_INTERNAL_RUN_ERROR)
            raise

    return OfficialHolidaySyncResult(run=run, resolution=resolution)


def _mark_interrupted_boe_runs(target_year, *, at):
    return HolidaySyncRun.objects.filter(
        year=target_year,
        source_name=BOE_NATIONAL_SOURCE_NAME,
        finished_at__isnull=True,
    ).update(
        status=HolidaySyncRun.Status.FAILED,
        finished_at=at,
        error_detail=BOE_INTERRUPTED_RUN_ERROR,
    )


def _finish_failed_boe_run(run, *, error_detail):
    run.status = HolidaySyncRun.Status.FAILED
    run.finished_at = timezone.now()
    run.error_detail = error_detail
    run.save(update_fields=["status", "finished_at", "error_detail"])


def latest_boe_national_holiday_run(*, year: int | None = None, at=None):
    """Return the latest BOE run that has actually started by ``at``."""

    effective_at = at or timezone.now()
    runs = HolidaySyncRun.objects.filter(
        source_name=BOE_NATIONAL_SOURCE_NAME,
        started_at__lte=effective_at,
    )
    if year is not None:
        runs = runs.filter(year=year)
    return runs.first()


@contextmanager
def _boe_year_sync_guard(target_year):
    """Reject overlapping downloads for one year and always release the mutex."""
    database_connection = transaction.get_connection()
    conflict_message = (
        f"Ya hay una sincronización del BOE en curso para {target_year}. "
        "Espera a que termine antes de volver a intentarlo."
    )

    if database_connection.vendor == "postgresql":
        if database_connection.in_atomic_block:
            raise BoeSyncError(
                "La sincronización del BOE debe iniciarse fuera de una transacción."
            )

        with database_connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s, %s)",
                [BOE_ADVISORY_LOCK_NAMESPACE, target_year],
            )
            acquired = cursor.fetchone()[0]
        if not acquired:
            raise BoeSyncError(conflict_message)

        try:
            yield
        finally:
            released = False
            try:
                if database_connection.is_usable():
                    with database_connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(%s, %s)",
                            [BOE_ADVISORY_LOCK_NAMESPACE, target_year],
                        )
                        released = cursor.fetchone()[0]
            except Exception:
                released = False
            if not released:
                database_connection.close()
        return

    with _BOE_LOCAL_YEAR_LOCKS_GUARD:
        local_lock = _BOE_LOCAL_YEAR_LOCKS.setdefault(target_year, Lock())
    if not local_lock.acquire(blocking=False):
        raise BoeSyncError(conflict_message)
    try:
        yield
    finally:
        local_lock.release()


def _lock_all_business_calendars():
    """Lock every business calendar in one deterministic global order."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("El bloqueo global de agendas requiere una transacción atómica.")

    from apps.booking.calendar_locking import lock_business_calendar
    from apps.businesses.models import Business

    database_connection = transaction.get_connection()
    if database_connection.vendor == "postgresql":
        table_name = database_connection.ops.quote_name(Business._meta.db_table)
        with database_connection.cursor() as cursor:
            cursor.execute(f"LOCK TABLE {table_name} IN SHARE MODE")

    businesses = tuple(Business.objects.only("pk").order_by("pk"))
    return tuple(lock_business_calendar(business) for business in businesses)


def _lock_boe_reconciliation_transaction():
    """Serialize downloaded BOE reconciliations before taking the global SHARE lock."""

    database_connection = transaction.get_connection()
    if not database_connection.in_atomic_block:
        raise RuntimeError("La reconciliación BOE requiere una transacción atómica.")
    if database_connection.vendor != "postgresql":
        return

    with database_connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [BOE_TRANSACTION_ADVISORY_LOCK_ID],
        )


def _reconcile_national_holidays(target_year, resolution, holidays):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("La reconciliación BOE requiere una transacción atómica.")

    authoritative_by_date = {holiday.day: holiday for holiday in holidays}
    authoritative_dates = set(authoritative_by_date)
    counts = {"created": 0, "updated": 0, "removed": 0, "skipped": 0}

    legacy_demo_holidays = tuple(
        OfficialHoliday.objects.select_for_update()
        .filter(
            year=target_year,
            scope=OfficialHoliday.Scope.NATIONAL,
            source_name="Calendario local AgendaSalon",
            official_reference="PFM-LOCAL",
        )
        .order_by("date", "pk")
    )
    counts["removed"] += len(legacy_demo_holidays)
    if legacy_demo_holidays:
        OfficialHoliday.objects.filter(
            pk__in=[holiday.pk for holiday in legacy_demo_holidays]
        ).delete()

    boe_holidays = tuple(
        OfficialHoliday.objects.select_for_update()
        .filter(
            year=target_year,
            scope=OfficialHoliday.Scope.NATIONAL,
            source_name="BOE - calendario laboral nacional",
        )
        .order_by("date", "pk")
    )
    outdated = tuple(
        holiday for holiday in boe_holidays if holiday.date not in authoritative_dates
    )
    counts["removed"] += len(outdated)
    if outdated:
        OfficialHoliday.objects.filter(
            pk__in=[holiday.pk for holiday in outdated]
        ).delete()

    existing_by_date = {
        holiday.date: holiday
        for holiday in OfficialHoliday.objects.select_for_update()
        .filter(
            date__in=authoritative_dates,
            scope=OfficialHoliday.Scope.NATIONAL,
        )
        .order_by("date", "pk")
    }
    for holiday_import in holidays:
        existing = existing_by_date.get(holiday_import.day)
        if existing is None:
            existing_by_date[holiday_import.day] = OfficialHoliday.objects.create(
                date=holiday_import.day,
                name=holiday_import.name,
                scope=OfficialHoliday.Scope.NATIONAL,
                year=target_year,
                source_name="BOE - calendario laboral nacional",
                source_url=resolution.url_html,
                official_reference=resolution.identifier,
            )
            counts["created"] += 1
            continue

        if existing.source_name != "BOE - calendario laboral nacional":
            counts["skipped"] += 1
            continue

        changed = (
            existing.name != holiday_import.name
            or existing.year != target_year
            or existing.source_url != resolution.url_html
            or existing.official_reference != resolution.identifier
        )
        if not changed:
            counts["skipped"] += 1
            continue

        existing.name = holiday_import.name
        existing.year = target_year
        existing.source_url = resolution.url_html
        existing.official_reference = resolution.identifier
        existing.save(
            update_fields=[
                "name",
                "year",
                "source_url",
                "official_reference",
                "updated_at",
            ]
        )
        counts["updated"] += 1

    return counts


def _locked_affected_future_appointments(holidays, *, locked_calendars, at):
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("El resumen de impacto BOE requiere una transacción atómica.")

    from apps.booking.models import Appointment

    holiday_dates = tuple(sorted(holiday.day for holiday in holidays))
    enabled_business_ids = tuple(
        locked_calendar.business.pk
        for locked_calendar in locked_calendars
        if locked_calendar.settings.apply_national_holidays
    )
    if not holiday_dates or not enabled_business_ids:
        return 0, 0

    affected_rows = tuple(
        Appointment.objects.select_for_update(of=("self",))
        .filter(
            business_id__in=enabled_business_ids,
            status=Appointment.Status.CONFIRMED,
            starts_at__gt=at,
            starts_at__date__in=holiday_dates,
        )
        .order_by("business_id", "pk")
        .values_list("pk", "business_id")
    )
    affected_business_ids = {business_id for _pk, business_id in affected_rows}
    return len(affected_rows), len(affected_business_ids)

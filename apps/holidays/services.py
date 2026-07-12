from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import requests
from bs4 import BeautifulSoup
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.holidays.models import HolidaySyncRun, OfficialHoliday


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
        response = self.session.get(
            self.SEARCH_URL,
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
            timeout=20,
        )
        if response.status_code != 200:
            raise BoeSyncError(
                f"El buscador oficial del BOE ha devuelto {response.status_code}."
            )

        soup = BeautifulSoup(response.text, "html.parser")
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
        response = self.session.get(url_html, timeout=20)
        if response.status_code != 200:
            raise BoeSyncError(
                f"No se ha podido descargar la resolución del BOE ({response.status_code})."
            )
        return response.text

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
    run = HolidaySyncRun.objects.create(
        year=target_year,
        source_name="BOE - calendario laboral nacional",
        status=HolidaySyncRun.Status.FAILED,
        started_at=timezone.now(),
        created_by=created_by,
    )

    try:
        resolution, holidays = sync_service.fetch_national_holidays(target_year)
        counts = _reconcile_national_holidays(target_year, resolution, holidays)
        affected_appointments, affected_businesses = _affected_future_appointments(holidays)
    except Exception as error:
        run.finished_at = timezone.now()
        run.error_detail = str(error) or "Error desconocido durante la sincronización con BOE."
        run.save(update_fields=["finished_at", "error_detail"])
        raise

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
    return OfficialHolidaySyncResult(run=run, resolution=resolution)


def _reconcile_national_holidays(target_year, resolution, holidays):
    authoritative_by_date = {holiday.day: holiday for holiday in holidays}
    authoritative_dates = set(authoritative_by_date)
    counts = {"created": 0, "updated": 0, "removed": 0, "skipped": 0}

    with transaction.atomic():
        legacy_demo_holidays = OfficialHoliday.objects.select_for_update().filter(
            year=target_year,
            scope=OfficialHoliday.Scope.NATIONAL,
            source_name="Calendario local AgendaSalon",
            official_reference="PFM-LOCAL",
        )
        counts["removed"] += legacy_demo_holidays.count()
        legacy_demo_holidays.delete()

        boe_holidays = OfficialHoliday.objects.select_for_update().filter(
            year=target_year,
            scope=OfficialHoliday.Scope.NATIONAL,
            source_name="BOE - calendario laboral nacional",
        )
        outdated = boe_holidays.exclude(date__in=authoritative_dates)
        counts["removed"] += outdated.count()
        outdated.delete()

        existing_by_date = {
            holiday.date: holiday
            for holiday in OfficialHoliday.objects.select_for_update().filter(
                date__in=authoritative_dates,
                scope=OfficialHoliday.Scope.NATIONAL,
            )
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


def _affected_future_appointments(holidays):
    from apps.booking.models import Appointment

    holiday_dates = [holiday.day for holiday in holidays]
    appointments = Appointment.objects.filter(
        status=Appointment.Status.CONFIRMED,
        starts_at__gte=timezone.now(),
        starts_at__date__in=holiday_dates,
    ).filter(
        Q(business__calendar_settings__apply_national_holidays=True)
        | Q(business__calendar_settings__isnull=True)
    )
    return appointments.count(), appointments.values("business_id").distinct().count()

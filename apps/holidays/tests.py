from datetime import date, datetime, timedelta

from django.test import TestCase
from django.utils import timezone

from apps.booking.models import Appointment, BusinessCalendarSettings, WorkLine
from apps.businesses.models import Business
from apps.customers.models import BusinessClient
from apps.holidays.models import HolidaySyncRun, OfficialHoliday
from apps.holidays.services import (
    BoeHolidayResolution,
    BoeNationalHolidaySyncService,
    BoeSyncError,
    OfficialHolidayImport,
    sync_boe_national_holidays,
)


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class FixedHolidayService:
    def __init__(self, resolution, holidays):
        self.resolution = resolution
        self.holidays = tuple(holidays)

    def fetch_national_holidays(self, target_year):
        return self.resolution, self.holidays


class BoeNationalHolidaySyncServiceTests(TestCase):
    def test_fetch_uses_official_search_and_extracts_only_common_national_dates(self):
        search_html = """
            <ul>
              <li class="resultado-busqueda">
                <p>Ministerio de Trabajo y Economía Social</p>
                <p>Resolución de 17 de octubre de 2025, por la que se publica la relación de fiestas laborales para el año 2026.</p>
                <a title="Ref. BOE-A-2025-21667">Más</a>
              </li>
            </ul>
        """
        rows = "".join(
            f"<tr><td>{day} Festivo {day}</td><td>*</td><td>**</td></tr>"
            for day in range(1, 6)
        )
        resolution_html = f"<table><tr><th>Enero</th></tr>{rows}<tr><td>6 Regional</td><td>*</td><td></td></tr></table>"
        session = FakeSession([FakeResponse(search_html), FakeResponse(resolution_html)])

        resolution, holidays = BoeNationalHolidaySyncService(session=session).fetch_national_holidays(2026)

        self.assertEqual(resolution.identifier, "BOE-A-2025-21667")
        self.assertEqual(len(holidays), 5)
        self.assertNotIn(date(2026, 1, 6), {holiday.day for holiday in holidays})
        self.assertEqual(session.calls[0][0], BoeNationalHolidaySyncService.SEARCH_URL)
        self.assertEqual(session.calls[0][1]["params"]["dato[6][0]"], "2025-01-01")

    def test_fetch_rejects_an_implausible_number_of_holidays(self):
        service = BoeNationalHolidaySyncService(
            session=FakeSession(
                [
                    FakeResponse(
                        '<li class="resultado-busqueda"><p>Relación de fiestas laborales para el año 2026</p><a title="Ref. BOE-A-2025-1">Más</a></li>'
                    ),
                    FakeResponse("<table><tr><th>Enero</th></tr><tr><td>1 Año Nuevo</td><td>*</td></tr></table>"),
                ]
            )
        )

        with self.assertRaises(BoeSyncError):
            service.fetch_national_holidays(2026)


class NationalHolidayReconciliationTests(TestCase):
    def setUp(self):
        self.resolution = BoeHolidayResolution(
            identifier="BOE-A-2025-21667",
            title="Relación de fiestas laborales para el año 2026",
            url_html="https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-21667",
        )

    def test_sync_creates_updates_removes_and_preserves_manual_conflicts(self):
        OfficialHoliday.objects.create(
            date=date(2026, 1, 1),
            name="Nombre antiguo",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="BOE - calendario laboral nacional",
        )
        OfficialHoliday.objects.create(
            date=date(2026, 4, 23),
            name="Fecha retirada",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="BOE - calendario laboral nacional",
        )
        OfficialHoliday.objects.create(
            date=date(2026, 7, 10),
            name="Fiesta nacional",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="Calendario local AgendaSalon",
            official_reference="PFM-LOCAL",
        )
        manual = OfficialHoliday.objects.create(
            date=date(2026, 5, 1),
            name="Nombre manual protegido",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="Carga manual",
        )
        service = FixedHolidayService(
            self.resolution,
            (
                OfficialHolidayImport(date(2026, 1, 1), "Año Nuevo"),
                OfficialHolidayImport(date(2026, 5, 1), "Fiesta del Trabajo"),
                OfficialHolidayImport(date(2026, 8, 15), "Asunción de la Virgen"),
            ),
        )

        result = sync_boe_national_holidays(2026, service=service)

        manual.refresh_from_db()
        self.assertEqual(manual.name, "Nombre manual protegido")
        self.assertFalse(OfficialHoliday.objects.filter(date=date(2026, 4, 23)).exists())
        self.assertTrue(OfficialHoliday.objects.filter(date=date(2026, 8, 15)).exists())
        self.assertEqual(result.run.status, HolidaySyncRun.Status.SUCCESS)
        self.assertEqual(result.run.items_created, 1)
        self.assertEqual(result.run.items_updated, 1)
        self.assertEqual(result.run.items_removed, 2)
        self.assertEqual(result.run.items_skipped, 1)

    def test_sync_reports_future_appointments_without_changing_them(self):
        business = Business.objects.create(commercial_name="Salón Centro", slug="salon-centro")
        BusinessCalendarSettings.objects.create(business=business, apply_national_holidays=True)
        client = BusinessClient.objects.create(business=business, full_name="María López")
        line = WorkLine.objects.create(business=business, line_number=1)
        future_date = timezone.localdate() + timedelta(days=40)
        starts_at = timezone.make_aware(
            datetime.combine(future_date, datetime.min.time().replace(hour=10))
        )
        appointment = Appointment.objects.create(
            business=business,
            business_client=client,
            work_line=line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
        )
        service = FixedHolidayService(
            BoeHolidayResolution(
                identifier="BOE-A-TEST-1",
                title="Calendario de prueba",
                url_html="https://www.boe.es/diario_boe/txt.php?id=BOE-A-TEST-1",
            ),
            (OfficialHolidayImport(future_date, "Festivo nacional"),),
        )

        result = sync_boe_national_holidays(future_date.year, service=service)

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CONFIRMED)
        self.assertEqual(result.run.affected_appointments, 1)
        self.assertEqual(result.run.affected_businesses, 1)

    def test_failed_sync_leaves_a_failure_run(self):
        class FailingService:
            def fetch_national_holidays(self, target_year):
                raise BoeSyncError("BOE no disponible")

        with self.assertRaises(BoeSyncError):
            sync_boe_national_holidays(2026, service=FailingService())

        run = HolidaySyncRun.objects.get()
        self.assertEqual(run.status, HolidaySyncRun.Status.FAILED)
        self.assertEqual(run.error_detail, "BOE no disponible")
        self.assertIsNotNone(run.finished_at)

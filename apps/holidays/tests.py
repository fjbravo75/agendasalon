from datetime import date, datetime, timedelta
from unittest.mock import patch

import requests
from django.db import transaction
from django.test import TestCase, TransactionTestCase
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
        self.headers = {}
        self.encoding = "utf-8"

    def iter_content(self, chunk_size):
        content = self.text.encode(self.encoding)
        for offset in range(0, len(content), chunk_size):
            yield content[offset : offset + chunk_size]


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
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        self.assertTrue(session.calls[0][1]["stream"])

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

    def test_fetch_rejects_redirects_and_oversized_responses(self):
        redirected = FakeResponse("", status_code=302)
        redirected.headers["Location"] = "http://127.0.0.1/internal"
        with self.assertRaises(BoeSyncError):
            BoeNationalHolidaySyncService(
                session=FakeSession([redirected])
            ).find_resolution(2026)

        oversized = FakeResponse("x" * (BoeNationalHolidaySyncService.MAX_RESPONSE_BYTES + 1))
        with self.assertRaises(BoeSyncError):
            BoeNationalHolidaySyncService(
                session=FakeSession([oversized])
            ).find_resolution(2026)

    def test_network_timeout_is_explained_without_technical_details(self):
        class TimeoutSession:
            def get(self, url, **kwargs):
                raise requests.Timeout(
                    "HTTPSConnectionPool(host='www.boe.es'): Read timed out."
                )

        with self.assertRaisesMessage(
            BoeSyncError,
            "No hemos podido consultar el BOE. "
            "Comprueba la conexión y vuelve a intentarlo.",
        ):
            BoeNationalHolidaySyncService(
                session=TimeoutSession()
            ).find_resolution(2026)


class NationalHolidayReconciliationTests(TransactionTestCase):
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

    @patch("apps.holidays.services.timezone.now")
    def test_new_sync_marks_an_unfinished_previous_run_as_interrupted(
        self,
        mocked_now,
    ):
        current_time = timezone.make_aware(datetime(2026, 7, 17, 8, 0))
        mocked_now.return_value = current_time
        interrupted_run = HolidaySyncRun.objects.create(
            year=2026,
            source_name="BOE - calendario laboral nacional",
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=current_time - timedelta(minutes=20),
            finished_at=None,
        )
        service = FixedHolidayService(
            self.resolution,
            (OfficialHolidayImport(date(2026, 1, 1), "Año Nuevo"),),
        )

        result = sync_boe_national_holidays(2026, service=service)

        interrupted_run.refresh_from_db()
        self.assertEqual(interrupted_run.status, HolidaySyncRun.Status.FAILED)
        self.assertEqual(interrupted_run.finished_at, current_time)
        self.assertEqual(
            interrupted_run.error_detail,
            "La sincronización anterior se interrumpió antes de terminar. "
            "No se aplicó un resultado completo.",
        )
        self.assertNotEqual(result.run.pk, interrupted_run.pk)
        self.assertEqual(result.run.status, HolidaySyncRun.Status.SUCCESS)

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

    def test_sync_snapshot_excludes_an_appointment_at_the_exact_capture_instant(self):
        business = Business.objects.create(
            commercial_name="Salón Frontera",
            slug="salon-frontera",
        )
        BusinessCalendarSettings.objects.create(
            business=business,
            apply_national_holidays=True,
        )
        client = BusinessClient.objects.create(
            business=business,
            full_name="Cliente Frontera",
        )
        exact_line = WorkLine.objects.create(
            business=business,
            line_number=1,
            name="Línea exacta",
        )
        future_line = WorkLine.objects.create(
            business=business,
            line_number=2,
            name="Línea futura",
        )
        holiday_date = timezone.localdate() + timedelta(days=40)
        captured_at = timezone.make_aware(
            datetime.combine(holiday_date, datetime.min.time().replace(hour=10))
        )
        for line, starts_at in (
            (exact_line, captured_at),
            (future_line, captured_at + timedelta(minutes=1)),
        ):
            Appointment.objects.create(
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
                identifier="BOE-A-TEST-EXACT-BOUNDARY",
                title="Calendario de frontera",
                url_html=(
                    "https://www.boe.es/diario_boe/"
                    "txt.php?id=BOE-A-TEST-EXACT-BOUNDARY"
                ),
            ),
            (OfficialHolidayImport(holiday_date, "Festivo nacional"),),
        )

        with patch(
            "apps.holidays.services.timezone.now",
            return_value=captured_at,
        ):
            result = sync_boe_national_holidays(
                holiday_date.year,
                service=service,
            )

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

    def test_network_failure_run_keeps_only_the_human_explanation(self):
        class TimeoutSession:
            def get(self, url, **kwargs):
                raise requests.Timeout("HTTPSConnectionPool: Read timed out")

        with self.assertRaises(BoeSyncError):
            sync_boe_national_holidays(
                2026,
                service=BoeNationalHolidaySyncService(session=TimeoutSession()),
            )

        run = HolidaySyncRun.objects.get()
        self.assertEqual(
            run.error_detail,
            "No hemos podido consultar el BOE. "
            "Comprueba la conexión y vuelve a intentarlo.",
        )
        self.assertNotIn("HTTPSConnectionPool", run.error_detail)

    def test_failure_during_impact_snapshot_rolls_back_the_catalogue(self):
        existing = OfficialHoliday.objects.create(
            date=date(2026, 1, 1),
            name="Nombre anterior",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="BOE - calendario laboral nacional",
            official_reference="BOE-A-OLD",
        )
        service = FixedHolidayService(
            self.resolution,
            (
                OfficialHolidayImport(date(2026, 1, 1), "Año Nuevo"),
                OfficialHolidayImport(date(2026, 8, 15), "Asunción de la Virgen"),
            ),
        )

        with patch(
            "apps.holidays.services._locked_affected_future_appointments",
            side_effect=RuntimeError("No se pudo construir el resumen"),
        ), self.assertRaises(RuntimeError):
            sync_boe_national_holidays(2026, service=service)

        existing.refresh_from_db()
        run = HolidaySyncRun.objects.get()
        self.assertEqual(existing.name, "Nombre anterior")
        self.assertEqual(existing.official_reference, "BOE-A-OLD")
        self.assertFalse(
            OfficialHoliday.objects.filter(date=date(2026, 8, 15)).exists()
        )
        self.assertEqual(run.status, HolidaySyncRun.Status.FAILED)
        self.assertEqual(run.items_created, 0)
        self.assertEqual(run.items_updated, 0)
        self.assertEqual(
            run.error_detail,
            "La sincronización se interrumpió por un error interno. "
            "No se aplicó un resultado completo.",
        )
        self.assertNotIn("No se pudo construir el resumen", run.error_detail)

    def test_sync_snapshot_counts_only_businesses_that_apply_national_holidays(self):
        future_date = timezone.localdate() + timedelta(days=40)
        starts_at = timezone.make_aware(
            datetime.combine(future_date, datetime.min.time().replace(hour=10))
        )
        for index, applies_holidays in enumerate((True, False), start=1):
            business = Business.objects.create(
                commercial_name=f"Salón {index}",
                slug=f"salon-{index}",
            )
            BusinessCalendarSettings.objects.create(
                business=business,
                apply_national_holidays=applies_holidays,
            )
            client = BusinessClient.objects.create(
                business=business,
                full_name=f"Cliente {index}",
            )
            line = WorkLine.objects.create(business=business, line_number=1)
            Appointment.objects.create(
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
            self.resolution,
            (OfficialHolidayImport(future_date, "Festivo nacional"),),
        )

        result = sync_boe_national_holidays(future_date.year, service=service)

        self.assertEqual(result.run.affected_appointments, 1)
        self.assertEqual(result.run.affected_businesses, 1)


class HolidaySyncTransactionBoundaryTests(TransactionTestCase):
    def test_external_fetch_happens_before_the_atomic_reconciliation(self):
        resolution = BoeHolidayResolution(
            identifier="BOE-A-TEST-BOUNDARY",
            title="Calendario de prueba",
            url_html="https://www.boe.es/diario_boe/txt.php?id=BOE-A-TEST-BOUNDARY",
        )

        class BoundaryInspectingService:
            def fetch_national_holidays(inner_self, target_year):
                self.assertFalse(transaction.get_connection().in_atomic_block)
                return resolution, (
                    OfficialHolidayImport(date(target_year, 1, 1), "Año Nuevo"),
                )

        result = sync_boe_national_holidays(
            2026,
            service=BoundaryInspectingService(),
        )

        self.assertEqual(result.run.status, HolidaySyncRun.Status.SUCCESS)
        self.assertEqual(result.run.items_created, 1)

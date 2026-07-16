from datetime import date, datetime, time, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment, AvailabilityRule, BusinessClosure, WorkLine
from apps.businesses.models import Business, BusinessActivityEvent
from apps.holidays.models import HolidaySyncRun, OfficialHoliday


class ProfessionalScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        cls.business = Business.objects.get(slug="peluqueria-mari")
        cls.other_business = Business.objects.get(slug="barberia-norte")
        cls.professional = get_user_model().objects.get(normalized_phone="+34600111001")

    def test_schedule_requires_login(self):
        response = self.client.get(reverse("booking:professional_schedule"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_schedule_loads_for_professional_business(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_schedule"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Horarios y capacidad")
        self.assertContains(response, "Peluquería Mari")
        self.assertContains(response, "Semana tipo")
        self.assertContains(response, "Líneas de trabajo")
        self.assertContains(response, "Cierres y bloqueos")
        self.assertContains(response, "Festivos nacionales")
        self.assertNotContains(response, "Barbería Norte")
        self.assertNotContains(response, "MVP")

    def test_professional_can_disable_national_holidays_for_own_business(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:professional_national_holidays_update"),
            {"apply_national_holidays": "false"},
            follow=True,
        )

        self.business.calendar_settings.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.business.calendar_settings.apply_national_holidays)
        self.assertContains(response, "La agenda permanecerá abierta en festivos nacionales")
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=BusinessActivityEvent.EventType.NATIONAL_HOLIDAYS_DISABLED,
            ).exists()
        )

    def test_cannot_enable_national_holidays_over_confirmed_pending_appointment(self):
        self.client.force_login(self.professional)
        calendar_settings = self.business.calendar_settings
        calendar_settings.apply_national_holidays = False
        calendar_settings.save(update_fields=["apply_national_holidays"])
        target_date = timezone.localdate() + timedelta(days=45)
        OfficialHoliday.objects.create(
            date=target_date,
            name="Festivo nacional de prueba",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=target_date.year,
            source_name="BOE - prueba",
            official_reference="BOE-TEST-CONFLICT",
        )
        starts_at = timezone.make_aware(
            datetime.combine(target_date, time(10, 0)),
            timezone.get_current_timezone(),
        )
        Appointment.objects.create(
            business=self.business,
            business_client=self.business.clients.first(),
            work_line=self.business.work_lines.filter(is_active=True).first(),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )

        response = self.client.post(
            reverse("booking:professional_national_holidays_update"),
            {"apply_national_holidays": "true"},
            follow=True,
        )
        calendar_settings.refresh_from_db()

        self.assertFalse(calendar_settings.apply_national_holidays)
        self.assertContains(
            response,
            "No puedes aplicar los festivos nacionales porque hay una cita confirmada",
        )

    def test_schedule_shows_upcoming_national_holidays_and_source_trace(self):
        holiday = OfficialHoliday.objects.create(
            date=date(2027, 1, 1),
            name="Año Nuevo",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2027,
            source_name="BOE - calendario laboral nacional",
            official_reference="BOE-A-2026-TEST",
        )
        HolidaySyncRun.objects.create(
            year=2027,
            source_name="BOE - calendario laboral nacional",
            official_reference="BOE-A-2026-TEST",
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            items_loaded=1,
        )
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_schedule"))

        self.assertContains(response, holiday.name)
        self.assertContains(response, "BOE-A-2026-TEST")

    def test_national_holiday_setting_rejects_get(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_national_holidays_update"))

        self.assertEqual(response.status_code, 405)

    def test_professional_can_create_availability_rule(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:professional_schedule"),
            {
                "form_kind": "availability",
                "availability-weekday": "6",
                "availability-start_time": "10:00",
                "availability-end_time": "13:00",
                "availability-is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AvailabilityRule.objects.filter(
                business=self.business,
                weekday=6,
                start_time=time(10, 0),
                end_time=time(13, 0),
                is_active=True,
            ).exists()
        )
        rule = AvailabilityRule.objects.get(
            business=self.business,
            weekday=6,
            start_time=time(10, 0),
            end_time=time(13, 0),
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_id=rule.id,
                event_type=BusinessActivityEvent.EventType.AVAILABILITY_CREATED,
            ).exists()
        )

    def test_availability_rejects_overlapping_rule(self):
        self.client.force_login(self.professional)
        existing_rule = self.business.availability_rules.filter(is_active=True).first()

        response = self.client.post(
            reverse("booking:professional_schedule"),
            {
                "form_kind": "availability",
                "availability-weekday": existing_rule.weekday,
                "availability-start_time": existing_rule.start_time.strftime("%H:%M"),
                "availability-end_time": existing_rule.end_time.strftime("%H:%M"),
                "availability-is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "se solapa con otro horario activo")

    def test_professional_can_create_business_closure(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:professional_schedule"),
            {
                "form_kind": "closure",
                "closure-closure_type": BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
                "closure-date_from": "2026-08-03",
                "closure-date_to": "2026-08-03",
                "closure-start_time": "12:00",
                "closure-end_time": "13:00",
                "closure-work_line": "",
                "closure-internal_reason": "Formación interna",
                "closure-is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        closure = BusinessClosure.objects.get(
            business=self.business,
            date_from=date(2026, 8, 3),
            start_time=time(12, 0),
        )
        self.assertEqual(closure.created_by, self.professional)
        self.assertIsNone(closure.work_line)
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_id=closure.id,
                event_type=BusinessActivityEvent.EventType.CLOSURE_CREATED,
            ).exists()
        )

    def test_schedule_forms_use_unique_prefixed_ids(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_schedule"))
        html = response.content.decode()

        self.assertEqual(html.count('id="id_availability-start_time"'), 1)
        self.assertEqual(html.count('id="id_closure-start_time"'), 1)
        self.assertEqual(html.count('id="id_availability-end_time"'), 1)
        self.assertEqual(html.count('id="id_closure-end_time"'), 1)
        self.assertNotIn('id="id_start_time"', html)

    def test_professional_cannot_edit_other_business_line(self):
        self.client.force_login(self.professional)
        other_line = WorkLine.objects.filter(business=self.other_business).first()

        response = self.client.get(reverse("booking:professional_work_line_edit", args=[other_line.id]))

        self.assertEqual(response.status_code, 404)

    def test_cannot_pause_line_with_future_confirmed_appointments(self):
        self.client.force_login(self.professional)
        line = self.business.work_lines.filter(is_active=True).first()
        client = self.business.clients.first()
        starts_at = timezone.now() + timedelta(days=30)
        Appointment.objects.create(
            business=self.business,
            business_client=client,
            work_line=line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )

        response = self.client.post(reverse("booking:professional_work_line_toggle", args=[line.id]), follow=True)
        line.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(line.is_active)
        self.assertContains(response, "tiene citas confirmadas pendientes")

    def test_cannot_pause_line_with_confirmed_appointment_already_in_progress(self):
        self.client.force_login(self.professional)
        line = self.business.work_lines.filter(is_active=True).first()
        starts_at = timezone.now() - timedelta(minutes=10)
        Appointment.objects.create(
            business=self.business,
            business_client=self.business.clients.first(),
            work_line=line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )

        response = self.client.post(
            reverse("booking:professional_work_line_toggle", args=[line.id]),
            follow=True,
        )
        line.refresh_from_db()

        self.assertTrue(line.is_active)
        self.assertContains(response, "tiene citas confirmadas pendientes")

    def test_cannot_create_closure_over_confirmed_pending_appointment(self):
        self.client.force_login(self.professional)
        line = self.business.work_lines.filter(is_active=True).first()
        target_date = timezone.localdate() + timedelta(days=30)
        starts_at = timezone.make_aware(
            datetime.combine(target_date, time(12, 0)),
            timezone.get_current_timezone(),
        )
        Appointment.objects.create(
            business=self.business,
            business_client=self.business.clients.first(),
            work_line=line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )

        response = self.client.post(
            reverse("booking:professional_schedule"),
            {
                "form_kind": "closure",
                "closure-closure_type": BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
                "closure-date_from": target_date.isoformat(),
                "closure-date_to": target_date.isoformat(),
                "closure-start_time": "12:00",
                "closure-end_time": "13:00",
                "closure-work_line": "",
                "closure-internal_reason": "Formación interna",
                "closure-is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            BusinessClosure.objects.filter(
                business=self.business,
                date_from=target_date,
                start_time=time(12, 0),
            ).exists()
        )
        self.assertContains(response, "No puedes aplicar este cierre porque se solapa")

    def test_cannot_reactivate_closure_over_confirmed_pending_appointment(self):
        self.client.force_login(self.professional)
        line = self.business.work_lines.filter(is_active=True).first()
        target_date = timezone.localdate() + timedelta(days=31)
        starts_at = timezone.make_aware(
            datetime.combine(target_date, time(10, 0)),
            timezone.get_current_timezone(),
        )
        Appointment.objects.create(
            business=self.business,
            business_client=self.business.clients.first(),
            work_line=line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )
        closure = BusinessClosure.objects.create(
            business=self.business,
            work_line=line,
            date_from=target_date,
            date_to=target_date,
            start_time=time(10, 0),
            end_time=time(11, 0),
            closure_type=BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
            is_active=False,
            created_by=self.professional,
        )

        response = self.client.post(
            reverse("booking:professional_closure_toggle", args=[closure.id]),
            follow=True,
        )
        closure.refresh_from_db()

        self.assertFalse(closure.is_active)
        self.assertContains(response, "No puedes aplicar este cierre porque se solapa")

    def test_cannot_pause_only_schedule_covering_confirmed_pending_appointment(self):
        self.client.force_login(self.professional)
        rule = self.business.availability_rules.filter(is_active=True).order_by("pk").first()
        target_date = self._next_date_for_weekday(rule.weekday)
        starts_at = timezone.make_aware(
            datetime.combine(target_date, rule.start_time),
            timezone.get_current_timezone(),
        )
        Appointment.objects.create(
            business=self.business,
            business_client=self.business.clients.first(),
            work_line=self.business.work_lines.filter(is_active=True).first(),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=15),
            total_duration_minutes=15,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )

        response = self.client.post(
            reverse("booking:professional_availability_toggle", args=[rule.id]),
            follow=True,
        )
        rule.refresh_from_db()

        self.assertTrue(rule.is_active)
        self.assertContains(response, "quedaría fuera de todos los tramos activos")

    def test_cannot_shrink_schedule_past_confirmed_pending_appointment(self):
        self.client.force_login(self.professional)
        rule = (
            self.business.availability_rules.filter(is_active=True)
            .order_by("weekday", "start_time", "pk")
            .first()
        )
        target_date = self._next_date_for_weekday(rule.weekday)
        starts_at = timezone.make_aware(
            datetime.combine(target_date, rule.start_time),
            timezone.get_current_timezone(),
        )
        Appointment.objects.create(
            business=self.business,
            business_client=self.business.clients.first(),
            work_line=self.business.work_lines.filter(is_active=True).first(),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=15),
            total_duration_minutes=15,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )
        original_start_time = rule.start_time
        later_start = (
            datetime.combine(date.min, original_start_time) + timedelta(minutes=15)
        ).time()

        response = self.client.post(
            reverse("booking:professional_availability_edit", args=[rule.id]),
            {
                "availability-weekday": rule.weekday,
                "availability-start_time": later_start.strftime("%H:%M"),
                "availability-end_time": rule.end_time.strftime("%H:%M"),
                "availability-is_active": "on",
            },
        )
        rule.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(rule.start_time, original_start_time)
        self.assertContains(response, "quedaría fuera de todos los tramos activos")

    @staticmethod
    def _next_date_for_weekday(weekday):
        today = timezone.localdate()
        days_ahead = (weekday - today.weekday()) % 7
        return today + timedelta(days=days_ahead or 7)

from datetime import date, time, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment, AvailabilityRule, BusinessClosure, WorkLine
from apps.businesses.models import Business


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
        self.assertNotContains(response, "Barbería Norte")
        self.assertNotContains(response, "MVP")

    def test_professional_can_create_availability_rule(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:professional_schedule"),
            {
                "form_kind": "availability",
                "weekday": "6",
                "start_time": "10:00",
                "end_time": "13:00",
                "is_active": "on",
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

    def test_availability_rejects_overlapping_rule(self):
        self.client.force_login(self.professional)
        existing_rule = self.business.availability_rules.filter(is_active=True).first()

        response = self.client.post(
            reverse("booking:professional_schedule"),
            {
                "form_kind": "availability",
                "weekday": existing_rule.weekday,
                "start_time": existing_rule.start_time.strftime("%H:%M"),
                "end_time": existing_rule.end_time.strftime("%H:%M"),
                "is_active": "on",
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
                "closure_type": BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
                "date_from": "2026-08-03",
                "date_to": "2026-08-03",
                "start_time": "12:00",
                "end_time": "13:00",
                "work_line": "",
                "internal_reason": "Formación interna",
                "is_active": "on",
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
        self.assertContains(response, "tiene citas futuras confirmadas")

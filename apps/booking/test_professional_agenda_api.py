from datetime import date, datetime
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.booking.models import Appointment
from apps.businesses.models import Business


class ProfessionalAgendaApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        cls.business = Business.objects.get(slug="peluqueria-mari")
        cls.other_business = Business.objects.get(slug="barberia-norte")
        cls.professional = get_user_model().objects.get(normalized_phone="+34600111001")
        cls.other_professional = get_user_model().objects.get(normalized_phone="+34600222001")
        cls.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34600999999",
            password="test-pass-123",
            full_name="Administración de AgendaSalon",
        )

    def setUp(self):
        self.now = datetime(2026, 7, 9, 8, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        self.api_now_patcher = patch("apps.booking.api.timezone.now", return_value=self.now)
        self.api_now_patcher.start()
        self.addCleanup(self.api_now_patcher.stop)

    def test_day_endpoint_requires_internal_login(self):
        response = self.client.get(reverse("booking:professional_agenda_day_data"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_day_endpoint_rejects_user_without_professional_business(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("booking:professional_agenda_day_data"))

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "professional_business_required")

    def test_day_endpoint_is_read_only(self):
        self.client.force_login(self.professional)

        response = self.client.post(reverse("booking:professional_agenda_day_data"))

        self.assertEqual(response.status_code, 405)

    def test_day_endpoint_returns_real_operational_data(self):
        self.client.force_login(self.professional)

        response = self.client.get(
            reverse("booking:professional_agenda_day_data"),
            {"date": "2026-07-09", "duration": "60"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("Cookie", response["Vary"])

        payload = response.json()
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["business"]["id"], self.business.id)
        self.assertEqual(payload["business"]["name"], "Peluquería Mari")
        self.assertEqual(payload["query"], {"date": "2026-07-09", "duration_minutes": 60})
        self.assertEqual(payload["calendar"]["timezone"], "Europe/Madrid")
        self.assertEqual(payload["calendar"]["slot_interval_minutes"], 15)
        self.assertTrue(payload["work_lines"])
        self.assertIsNotNone(payload["recommended_slot"])
        self.assertTrue(payload["suggestions"])

        appointments = [
            appointment
            for line in payload["work_lines"]
            for appointment in line["appointments"]
        ]
        self.assertTrue(appointments)
        self.assertTrue(
            any(appointment["service_summary"] for appointment in appointments)
        )
        self.assertTrue(
            any(line["available_slots"] for line in payload["work_lines"])
        )

    def test_day_endpoint_never_exposes_another_business(self):
        self.client.force_login(self.professional)
        other_appointment_ids = set(
            Appointment.objects.filter(business=self.other_business).values_list("id", flat=True)
        )

        response = self.client.get(
            reverse("booking:professional_agenda_day_data"),
            {"date": "2026-07-09", "duration": "60"},
        )

        payload = response.json()
        returned_appointment_ids = {
            appointment["id"]
            for line in payload["work_lines"]
            for appointment in line["appointments"]
        }
        self.assertTrue(returned_appointment_ids)
        self.assertTrue(returned_appointment_ids.isdisjoint(other_appointment_ids))
        self.assertNotEqual(payload["business"]["id"], self.other_business.id)

    def test_day_endpoint_resolves_business_from_authenticated_membership(self):
        self.client.force_login(self.other_professional)

        response = self.client.get(
            reverse("booking:professional_agenda_day_data"),
            {"date": "2026-07-09", "duration": "30"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["business"]["id"], self.other_business.id)
        self.assertEqual(payload["business"]["name"], "Barbería Norte")
        self.assertNotEqual(payload["business"]["id"], self.business.id)

    def test_day_endpoint_validates_date_and_duration(self):
        self.client.force_login(self.professional)
        endpoint = reverse("booking:professional_agenda_day_data")

        invalid_date = self.client.get(endpoint, {"date": "09/07/2026"})
        invalid_duration = self.client.get(endpoint, {"duration": "50"})
        excessive_duration = self.client.get(endpoint, {"duration": "900"})

        self.assertEqual(invalid_date.status_code, 400)
        self.assertEqual(invalid_duration.status_code, 400)
        self.assertEqual(excessive_duration.status_code, 400)
        self.assertEqual(invalid_date.json()["error"]["code"], "invalid_query")
        self.assertIn("AAAA-MM-DD", invalid_date.json()["error"]["message"])
        self.assertIn("15 minutos", invalid_duration.json()["error"]["message"])

    def test_day_endpoint_validates_duration_against_business_interval(self):
        self.business.calendar_settings.slot_interval_minutes = 30
        self.business.calendar_settings.save(update_fields=["slot_interval_minutes"])
        self.client.force_login(self.professional)

        response = self.client.get(
            reverse("booking:professional_agenda_day_data"),
            {"date": "2026-07-09", "duration": "15"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_query")
        self.assertIn("intervalo de agenda de 30 minutos", response.json()["error"]["message"])

    def test_day_endpoint_reports_closure_and_holiday_context(self):
        self.client.force_login(self.professional)

        closure_response = self.client.get(
            reverse("booking:professional_agenda_day_data"),
            {"date": "2026-07-06", "duration": "60"},
        )
        holiday_response = self.client.get(
            reverse("booking:professional_agenda_day_data"),
            {"date": "2026-07-10", "duration": "60"},
        )

        self.assertEqual(closure_response.status_code, 200)
        self.assertTrue(closure_response.json()["closures"])
        self.assertEqual(holiday_response.status_code, 200)
        self.assertEqual(holiday_response.json()["calendar"]["reason"], "festivo_nacional")
        self.assertEqual(holiday_response.json()["holidays"][0]["name"], "Fiesta nacional")

    def test_month_endpoint_requires_internal_login(self):
        response = self.client.get(reverse("booking:professional_agenda_month_data"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_month_endpoint_returns_one_entry_per_day(self):
        self.client.force_login(self.professional)

        response = self.client.get(
            reverse("booking:professional_agenda_month_data"),
            {"year": "2026", "month": "7", "duration": "60"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["business"]["id"], self.business.id)
        self.assertEqual(
            payload["query"],
            {"year": 2026, "month": 7, "duration_minutes": 60},
        )
        self.assertEqual(len(payload["days"]), 31)
        self.assertEqual(payload["days"][0]["date"], date(2026, 7, 1).isoformat())
        self.assertEqual(payload["days"][-1]["date"], date(2026, 7, 31).isoformat())
        self.assertTrue(any(day["first_slot"] for day in payload["days"]))

    def test_month_endpoint_validates_calendar_query(self):
        self.client.force_login(self.professional)
        endpoint = reverse("booking:professional_agenda_month_data")

        invalid_month = self.client.get(endpoint, {"year": "2026", "month": "13"})
        invalid_year = self.client.get(endpoint, {"year": "mil", "month": "7"})

        self.assertEqual(invalid_month.status_code, 400)
        self.assertEqual(invalid_year.status_code, 400)
        self.assertEqual(invalid_month.json()["error"]["code"], "invalid_query")
        self.assertIn("entre 1 y 12", invalid_month.json()["error"]["message"])

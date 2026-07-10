from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.businesses.models import Business


class ProfessionalAgendaReactViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        cls.business = Business.objects.get(slug="peluqueria-mari")
        cls.professional = get_user_model().objects.get(normalized_phone="+34600111001")
        cls.other_professional = get_user_model().objects.get(normalized_phone="+34600222001")

    def test_agenda_requires_internal_login(self):
        response = self.client.get(reverse("booking:professional_agenda"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_agenda_requires_active_professional_business(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600999111",
            password="test-pass-123",
            full_name="Profesional sin negocio",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("booking:professional_agenda"))

        self.assertRedirects(response, reverse("accounts:no_business"))

    def test_agenda_mounts_react_with_real_business_configuration(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_agenda"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peluquería Mari")
        self.assertContains(response, 'id="professional-agenda-root"')
        self.assertContains(response, 'id="professional-agenda-config"')
        self.assertContains(response, "static/react/agenda.css")
        self.assertContains(response, "static/react/agenda.js")
        config = response.context["agenda_config"]
        self.assertEqual(config["businessName"], "Peluquería Mari")
        self.assertEqual(config["dayEndpoint"], reverse("booking:professional_agenda_day_data"))
        self.assertEqual(config["monthEndpoint"], reverse("booking:professional_agenda_month_data"))
        self.assertEqual(config["slotIntervalMinutes"], 15)
        self.assertTrue(config["durationOptions"])
        self.assertTrue(all(duration % 15 == 0 for duration in config["durationOptions"]))
        self.assertIn(75, config["durationOptions"])
        self.assertIn(135, config["durationOptions"])
        self.assertIn(240, config["durationOptions"])

    def test_agenda_configuration_is_isolated_for_second_business(self):
        self.client.force_login(self.other_professional)

        response = self.client.get(reverse("booking:professional_agenda"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["business"].slug, "barberia-norte")
        self.assertEqual(response.context["agenda_config"]["businessName"], "Barbería Norte")
        self.assertNotContains(response, "Peluquería Mari")

    def test_professional_navigation_separates_summary_and_agenda(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_agenda"))

        self.assertContains(response, ">Resumen</a>", html=False)
        self.assertContains(response, 'aria-current="page">Agenda</a>', html=False)

    def test_selected_slot_survives_first_step_in_new_appointment(self):
        self.client.force_login(self.professional)
        starts_at = "2026-07-13T10:15:00+02:00"

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "target_date": "2026-07-13",
                "selected_work_line_id": "2",
                "selected_starts_at": starts_at,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<input type="hidden" name="selected_work_line_id" value="2">',
            html=True,
        )
        self.assertContains(
            response,
            f'<input type="hidden" name="selected_starts_at" value="{starts_at}">',
            html=True,
        )

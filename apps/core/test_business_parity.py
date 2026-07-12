from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse


class ActiveBusinessParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        User = get_user_model()
        cls.professionals = (
            User.objects.get(normalized_phone="+34600111001"),
            User.objects.get(normalized_phone="+34600222001"),
        )

    def test_professional_surfaces_share_templates_and_structure(self):
        routes = (
            ("dashboards:professional_home", "professional/home.html", "professional-dashboard"),
            ("booking:professional_agenda", "professional/agenda.html", "professional-agenda-root"),
            ("booking:appointment_assistant", "professional/appointment_assistant.html", "assistant-shell"),
            (
                "booking:professional_pending_appointments",
                "professional/appointments/pending.html",
                "professional-dashboard",
            ),
            ("booking:professional_service_list", "professional/services/list.html", "services-shell"),
            ("booking:professional_schedule", "professional/schedule.html", "schedule-shell"),
            ("customers:professional_client_list", "professional/clients/list.html", "clients-shell"),
            ("business_settings:professional_settings", "professional/settings.html", "settings-shell"),
        )

        for route_name, expected_template, structural_marker in routes:
            results = []
            for professional in self.professionals:
                self.client.force_login(professional)
                response = self.client.get(reverse(route_name))
                results.append(
                    (
                        response.status_code,
                        expected_template
                        in tuple(template.name for template in response.templates if template.name),
                        structural_marker in response.content.decode(),
                    )
                )

            self.assertEqual(results[0], results[1], route_name)
            self.assertEqual(results[0][0], 200, route_name)
            self.assertTrue(results[0][1], route_name)
            self.assertTrue(results[0][2], route_name)

    def test_public_surfaces_share_templates_and_controls(self):
        businesses = ("peluqueria-mari", "barberia-norte")
        route_specs = (
            ("public_booking", "public/booking.html", "data-booking-search"),
            ("customers:client_access", "customers/client_access.html", "client-auth-shell"),
            ("customers:client_register", "customers/client_register.html", "client-auth-shell"),
        )

        for route_name, expected_template, structural_marker in route_specs:
            results = []
            for slug in businesses:
                response = self.client.get(reverse(route_name, args=[slug]))
                results.append(
                    (
                        response.status_code,
                        expected_template
                        in tuple(template.name for template in response.templates if template.name),
                        structural_marker in response.content.decode(),
                    )
                )

            self.assertEqual(results[0], results[1], route_name)
            self.assertEqual(results[0][0], 200, route_name)
            self.assertTrue(results[0][1], route_name)
            self.assertTrue(results[0][2], route_name)

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.booking.models import Service
from apps.businesses.models import Business


class ProfessionalServiceManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        cls.business = Business.objects.get(slug="peluqueria-mari")
        cls.other_business = Business.objects.get(slug="barberia-norte")
        cls.professional = get_user_model().objects.get(normalized_phone="+34600111001")

    def test_service_list_requires_login(self):
        response = self.client.get(reverse("booking:professional_service_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_service_list_loads_for_professional_business(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_service_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Servicios de Peluquería Mari")
        self.assertContains(response, "Catálogo de reserva")
        self.assertContains(response, "Lavado")
        self.assertContains(response, "Pausado")
        self.assertNotContains(response, "Barbería Norte")
        self.assertNotContains(response, "MVP")

    def test_professional_can_create_service_for_own_business(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:professional_service_list"),
            {
                "name": "Retoque de flequillo",
                "duration_minutes": "15",
                "price_amount": "9.50",
                "color_hex": "#5079bd",
                "display_order": "8",
                "description": "Ajuste rápido entre cortes completos.",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        service = Service.objects.get(
            business=self.business,
            name="Retoque de flequillo",
        )
        self.assertEqual(service.duration_minutes, 15)
        self.assertEqual(service.color_hex, "#5079BD")
        self.assertTrue(service.is_active)

    def test_service_form_rejects_duration_outside_slot_interval(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:professional_service_list"),
            {
                "name": "Servicio irregular",
                "duration_minutes": "20",
                "price_amount": "12.00",
                "color_hex": "#08927F",
                "display_order": "9",
                "description": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usa tramos de 15 minutos.")
        self.assertFalse(
            Service.objects.filter(
                business=self.business,
                name="Servicio irregular",
            ).exists()
        )

    def test_professional_can_edit_service(self):
        self.client.force_login(self.professional)
        service = self.business.services.get(name="Corte")

        response = self.client.post(
            reverse("booking:professional_service_edit", args=[service.id]),
            {
                "name": "Corte completo",
                "duration_minutes": "45",
                "price_amount": "22.00",
                "color_hex": "#5079BD",
                "display_order": "2",
                "description": "Corte con repaso final.",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        service.refresh_from_db()
        self.assertEqual(service.name, "Corte completo")
        self.assertEqual(service.duration_minutes, 45)
        self.assertEqual(service.price_amount.to_eng_string(), "22.00")

    def test_professional_cannot_edit_service_from_another_business(self):
        self.client.force_login(self.professional)
        service = self.other_business.services.first()

        response = self.client.get(
            reverse("booking:professional_service_edit", args=[service.id])
        )

        self.assertEqual(response.status_code, 404)

    def test_paused_service_disappears_from_new_appointment_form(self):
        self.client.force_login(self.professional)
        service = self.business.services.get(name="Lavado")

        response = self.client.post(
            reverse("booking:professional_service_toggle", args=[service.id])
        )

        self.assertEqual(response.status_code, 302)
        service.refresh_from_db()
        self.assertFalse(service.is_active)

        response = self.client.get(reverse("booking:appointment_assistant"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Lavado - 15 min")

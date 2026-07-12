from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.booking.models import Service
from apps.businesses.models import Business, BusinessActivityEvent


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
        self.assertContains(response, "data-service-color-picker")
        self.assertContains(response, "data-service-color-option", count=30)
        self.assertContains(response, "Color seleccionado")
        self.assertContains(response, "Elegir color")
        self.assertContains(response, 'type="hidden" name="color_hex"')
        self.assertNotContains(response, "Barbería Norte")
        self.assertNotContains(response, "MVP")

    def test_service_catalog_scrolls_when_there_are_more_than_five_services(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_service_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "service-list--scrollable")
        self.assertContains(response, "data-service-scroll-list")
        self.assertContains(response, "desplázate para consultar todos")

    def test_service_catalog_scroll_rule_is_shared_by_barberia_norte(self):
        secondary_professional = get_user_model().objects.get(
            normalized_phone="+34600222001"
        )
        self.client.force_login(secondary_professional)

        response = self.client.get(reverse("booking:professional_service_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Servicios de Barbería Norte")
        self.assertNotContains(response, "service-list--scrollable")
        self.assertNotContains(response, "data-service-scroll-list")

        next_order = self.other_business.services.count() + 1
        Service.objects.create(
            business=self.other_business,
            name="Servicio adicional de barbería",
            duration_minutes=15,
            display_order=next_order,
            color_hex="#5079BD",
        )

        response = self.client.get(reverse("booking:professional_service_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Servicios de Barbería Norte")
        self.assertContains(response, "service-list--scrollable")
        self.assertContains(response, "data-service-scroll-list")
        self.assertNotContains(response, "Peluquería Mari")

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
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_id=service.id,
                event_type=BusinessActivityEvent.EventType.SERVICE_CREATED,
                actor_user=self.professional,
            ).exists()
        )

    def test_service_form_rejects_a_color_outside_the_palette(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:professional_service_list"),
            {
                "name": "Servicio con color externo",
                "duration_minutes": "30",
                "price_amount": "12.00",
                "color_hex": "#123456",
                "display_order": "9",
                "description": "",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecciona un color de la paleta.")
        self.assertFalse(
            Service.objects.filter(
                business=self.business,
                name="Servicio con color externo",
            ).exists()
        )

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
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_id=service.id,
                event_type=BusinessActivityEvent.EventType.SERVICE_UPDATED,
            ).exists()
        )

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
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_id=service.id,
                event_type=BusinessActivityEvent.EventType.SERVICE_PAUSED,
            ).exists()
        )

        response = self.client.get(reverse("booking:appointment_assistant"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Lavado - 15 min")

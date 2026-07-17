from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.booking.models import Appointment, Service
from apps.businesses.models import Business, BusinessMembership
from apps.customers.models import BusinessClient


class P2SlotSelectionValidationTests(TestCase):
    INVALID_SLOT_MESSAGE = (
        "El hueco seleccionado no es válido. Vuelve a elegir una hora."
    )
    MISSING_SLOT_MESSAGE = "Elige un hueco para confirmar la cita."

    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Salón P2",
            slug="salon-p2",
            is_active=True,
            public_booking_enabled=True,
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600999001",
            full_name="Profesional P2",
            password="ProfesionalP2-2026!",
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
        )
        self.business_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente P2",
            phone="600999002",
        )
        self.service = Service.objects.create(
            business=self.business,
            name="Servicio P2",
            duration_minutes=30,
        )

    def _post_public_slot(self, **overrides):
        data = {
            "action": "choose_slot",
            "services": [self.service.pk],
            "target_date": "2026-07-20",
            "selected_work_line_id": "1",
            "selected_starts_at": "2026-07-20T10:00:00+02:00",
        }
        data.update(overrides)
        return self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            data,
        )

    def _assert_public_selection_error(self, response, message):
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn(message, form.non_field_errors())

        html = response.content.decode()
        alert_start = html.index('<div class="alert" role="alert">')
        alert_end = html.index("</div>", alert_start)
        self.assertIn(message, html[alert_start:alert_end])
        self.assertFalse(Appointment.objects.exists())

    def test_professional_non_numeric_work_line_is_visible_and_creates_no_appointment(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:appointment_assistant"),
            {
                "business_client": self.business_client.pk,
                "manual_channel": "telefono",
                "requested_by_contact": "self",
                "services": [self.service.pk],
                "target_date": "2026-07-20",
                "selected_work_line_id": "no-es-un-numero",
                "selected_starts_at": "2026-07-20T10:00:00+02:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.INVALID_SLOT_MESSAGE)
        self.assertIn(
            self.INVALID_SLOT_MESSAGE,
            response.context["form"].non_field_errors(),
        )
        self.assertFalse(Appointment.objects.exists())

    def test_public_non_numeric_work_line_is_visible_and_creates_no_appointment(self):
        response = self._post_public_slot(
            selected_work_line_id="no-es-un-numero",
        )

        form = response.context["form"]
        self.assertTrue(form.has_error("selected_work_line_id", "invalid"))
        self._assert_public_selection_error(
            response,
            self.INVALID_SLOT_MESSAGE,
        )

    def test_public_zero_work_line_is_visible_and_creates_no_appointment(self):
        response = self._post_public_slot(selected_work_line_id="0")

        form = response.context["form"]
        self.assertTrue(form.has_error("selected_work_line_id", "min_value"))
        self._assert_public_selection_error(
            response,
            self.INVALID_SLOT_MESSAGE,
        )

    def test_public_empty_selected_start_is_visible_and_creates_no_appointment(self):
        response = self._post_public_slot(selected_starts_at="")

        form = response.context["form"]
        self.assertTrue(form.has_error("selected_starts_at", "missing_slot"))
        self._assert_public_selection_error(
            response,
            self.MISSING_SLOT_MESSAGE,
        )

    def test_public_malformed_selected_start_is_visible_and_creates_no_appointment(self):
        response = self._post_public_slot(selected_starts_at="fecha-imposible")

        form = response.context["form"]
        self.assertTrue(form.has_error("selected_starts_at", "invalid_slot"))
        self._assert_public_selection_error(
            response,
            self.INVALID_SLOT_MESSAGE,
        )

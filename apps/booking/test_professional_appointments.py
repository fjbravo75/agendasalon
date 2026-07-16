from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment
from apps.businesses.models import Business, BusinessActivityEvent


class ProfessionalAppointmentManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        cls.business = Business.objects.get(slug="peluqueria-mari")
        cls.other_business = Business.objects.get(slug="barberia-norte")
        cls.professional = get_user_model().objects.get(normalized_phone="+34600111001")

    def test_appointment_detail_requires_login(self):
        response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[1])
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_appointment_detail_loads_for_professional_business(self):
        self.client.force_login(self.professional)
        appointment = self.business.appointments.filter(
            status=Appointment.Status.CONFIRMED,
        ).first()

        response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Gestión de cita")
        self.assertContains(response, appointment.business_client.full_name)
        self.assertContains(response, appointment.service_summary_snapshot)
        self.assertContains(response, "Trazabilidad")
        self.assertNotContains(response, "MVP")
        self.assertNotContains(response, "Barbería Norte")

    def test_professional_cannot_open_other_business_appointment(self):
        self.client.force_login(self.professional)
        appointment = self._create_appointment(business=self.other_business)

        response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.id])
        )

        self.assertEqual(response.status_code, 404)

    def test_professional_can_cancel_confirmed_appointment_with_reason(self):
        self.client.force_login(self.professional)
        appointment = self._create_appointment()

        response = self.client.post(
            reverse("booking:professional_appointment_cancel", args=[appointment.id]),
            {"cancellation_reason": "Cliente avisa por teléfono que no puede venir."},
        )

        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)
        self.assertEqual(appointment.cancelled_by, self.professional)
        self.assertIsNotNone(appointment.cancelled_at)
        self.assertEqual(
            appointment.cancellation_reason,
            "Cliente avisa por teléfono que no puede venir.",
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_id=appointment.id,
                event_type=BusinessActivityEvent.EventType.APPOINTMENT_CANCELLED,
                actor_user=self.professional,
            ).exists()
        )
        detail_response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.id])
        )
        self.assertContains(detail_response, "No hay avisos registrados para esta cita.")
        self.assertNotContains(detail_response, "La cita sigue confirmada.")

    def test_cancel_requires_reason_and_keeps_confirmed_status(self):
        self.client.force_login(self.professional)
        appointment = self._create_appointment()

        response = self.client.post(
            reverse("booking:professional_appointment_cancel", args=[appointment.id]),
            {"cancellation_reason": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Indica el motivo de cancelación.")
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CONFIRMED)
        self.assertEqual(appointment.cancellation_reason, "")

    def test_professional_can_complete_started_appointment(self):
        self.client.force_login(self.professional)
        appointment = self._create_appointment(
            starts_at=timezone.now() - timedelta(days=100),
        )

        response = self.client.post(
            reverse("booking:professional_appointment_complete", args=[appointment.id])
        )

        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.COMPLETED)
        self.assertEqual(appointment.completed_by, self.professional)
        self.assertIsNotNone(appointment.completed_at)
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_id=appointment.id,
                event_type=BusinessActivityEvent.EventType.APPOINTMENT_COMPLETED,
            ).exists()
        )

    def test_professional_can_mark_started_appointment_as_no_show(self):
        self.client.force_login(self.professional)
        appointment = self._create_appointment(
            starts_at=timezone.now() - timedelta(hours=2),
        )

        response = self.client.post(
            reverse("booking:professional_appointment_no_show", args=[appointment.id])
        )

        self.assertEqual(response.status_code, 302)
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.NO_SHOW)
        self.assertEqual(appointment.no_show_marked_by, self.professional)
        self.assertIsNotNone(appointment.no_show_marked_at)
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_id=appointment.id,
                event_type=BusinessActivityEvent.EventType.APPOINTMENT_NO_SHOW,
            ).exists()
        )

    def test_future_appointment_cannot_be_marked_as_no_show(self):
        self.client.force_login(self.professional)
        appointment = self._create_appointment(
            starts_at=timezone.now() + timedelta(days=2),
        )

        response = self.client.post(
            reverse("booking:professional_appointment_no_show", args=[appointment.id]),
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(appointment.status, Appointment.Status.CONFIRMED)
        self.assertContains(response, "antes de que empiece")

    def test_professional_can_close_multiple_pending_appointments_as_attended(self):
        self.client.force_login(self.professional)
        first = self._create_appointment(starts_at=timezone.now() - timedelta(days=100))
        second = self._create_appointment(starts_at=timezone.now() - timedelta(days=99))

        response = self.client.post(
            reverse("booking:professional_appointments_bulk_close"),
            {
                "appointment_ids": [first.id, second.id],
                "outcome": Appointment.Status.COMPLETED,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2 citas quedan registradas como atendidas")
        self.assertEqual(
            Appointment.objects.filter(
                pk__in=[first.id, second.id],
                status=Appointment.Status.COMPLETED,
            ).count(),
            2,
        )

    def test_bulk_close_ignores_appointments_from_another_business(self):
        self.client.force_login(self.professional)
        own_appointment = self._create_appointment(starts_at=timezone.now() - timedelta(days=100))
        other_appointment = self._create_appointment(
            business=self.other_business,
            starts_at=timezone.now() - timedelta(days=99),
        )

        response = self.client.post(
            reverse("booking:professional_appointments_bulk_close"),
            {
                "appointment_ids": [own_appointment.id, other_appointment.id],
                "outcome": Appointment.Status.NO_SHOW,
            },
        )

        self.assertEqual(response.status_code, 302)
        own_appointment.refresh_from_db()
        other_appointment.refresh_from_db()
        self.assertEqual(own_appointment.status, Appointment.Status.NO_SHOW)
        self.assertEqual(other_appointment.status, Appointment.Status.CONFIRMED)

    def test_future_appointment_cannot_be_completed(self):
        self.client.force_login(self.professional)
        appointment = self._create_appointment(
            starts_at=timezone.now() + timedelta(days=30),
        )

        detail_response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.id])
        )
        self.assertContains(detail_response, "Aún no ha empezado")

        response = self.client.post(
            reverse("booking:professional_appointment_complete", args=[appointment.id]),
            follow=True,
        )

        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(appointment.status, Appointment.Status.CONFIRMED)
        self.assertContains(response, "todavía no ha empezado")

    def _create_appointment(self, *, business=None, starts_at=None):
        business = business or self.business
        starts_at = starts_at or (timezone.now() + timedelta(days=14))
        line = business.work_lines.filter(is_active=True).first()
        business_client = business.clients.filter(is_active=True).first()
        appointment = Appointment(
            business=business,
            business_client=business_client,
            work_line=line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
            service_summary_snapshot="Corte",
        )
        appointment.full_clean()
        appointment.save()
        return appointment

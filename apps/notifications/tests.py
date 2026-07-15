from datetime import timedelta
from io import StringIO
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.booking.models import Appointment, WorkLine
from apps.businesses.models import Business, BusinessMembership
from apps.customers.services import register_client_access
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    cancel_appointment_emails,
    client_verification_url,
    queue_and_dispatch,
    queue_appointment_emails,
    queue_client_email_verification,
    queue_professional_activation,
    queue_professional_email_verification,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True,
    AGENDA_PLATFORM_WEBSITE="http://testserver",
    DEFAULT_FROM_EMAIL="AgendaSalon <agenda@example.test>",
)
class TransactionalEmailTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
            public_booking_enabled=True,
        )

    def test_sent_status_describes_provider_acceptance_without_claiming_delivery(self):
        delivery = OutboundEmail(status=OutboundEmail.Status.SENT)

        self.assertEqual(
            delivery.operational_status_label,
            "Aceptado por el servicio de correo",
        )

    def test_professional_activation_sets_a_private_password_and_verifies_email(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600999001",
            full_name="Profesional Nueva",
            email="profesional@example.test",
            password=None,
            is_active=False,
            email_verification_required=True,
        )
        BusinessMembership.objects.create(business=self.business, user=user)

        delivery = queue_and_dispatch(queue_professional_activation(user, business=self.business))

        self.assertEqual(delivery.status, OutboundEmail.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ya está preparado", mail.outbox[0].body)
        self.assertIn("contraseña personal", mail.outbox[0].body)
        activation_url = next(
            line for line in mail.outbox[0].body.splitlines() if line.startswith("http")
        )
        response = self.client.post(
            urlparse(activation_url).path,
            {
                "new_password1": "MiClavePersonal2026!",
                "new_password2": "MiClavePersonal2026!",
            },
        )

        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("MiClavePersonal2026!"))
        self.assertIsNotNone(user.email_verified_at)
        self.assertFalse(user.email_verification_required)

    def test_client_must_verify_email_before_login_and_token_is_one_time(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente Web",
            phone="600999101",
            email="cliente@example.test",
            password="ClienteDemo2026!",
        )
        delivery = queue_and_dispatch(queue_client_email_verification(access))
        verify_path = urlparse(client_verification_url(access)).path

        self.assertEqual(delivery.status, OutboundEmail.Status.SENT)
        response = self.client.get(verify_path)
        self.assertEqual(response.status_code, 302)
        access.refresh_from_db()
        self.assertIsNotNone(access.email_verified_at)
        self.assertEqual(self.client.session["business_client_access_id"], access.pk)

        replay = self.client_class().get(verify_path)
        self.assertEqual(replay.status_code, 410)

    def test_confirmation_and_reminder_are_idempotent_and_cancel_safely(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente con cita",
            phone="600999102",
            email="cita@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        line = WorkLine.objects.create(business=self.business, line_number=1, name="Línea 1")
        starts_at = timezone.now() + timedelta(days=3)
        appointment = Appointment.objects.create(
            business=self.business,
            business_client=access.business_client,
            work_line=line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=45),
            total_duration_minutes=45,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            requested_by_client_access=access,
            service_summary_snapshot="Corte y peinado",
        )

        first = queue_appointment_emails(appointment)
        second = queue_appointment_emails(appointment)

        self.assertEqual(len(first), 2)
        self.assertEqual({email.pk for email in first}, {email.pk for email in second})
        reminder = OutboundEmail.objects.get(kind=OutboundEmail.Kind.APPOINTMENT_REMINDER)
        self.assertAlmostEqual(
            reminder.scheduled_for,
            starts_at - timedelta(hours=24),
            delta=timedelta(seconds=1),
        )

        cancelled = cancel_appointment_emails(appointment)
        self.assertEqual(cancelled, 2)
        self.assertFalse(
            OutboundEmail.objects.filter(appointment=appointment, status=OutboundEmail.Status.PENDING).exists()
        )

    def test_professional_email_verification_can_be_resent(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600999002",
            full_name="Profesional Heredada",
            email="heredada@example.test",
            password="MiClavePersonal2026!",
            is_active=True,
            email_verification_required=True,
        )
        BusinessMembership.objects.create(business=self.business, user=user)

        first = queue_and_dispatch(
            queue_professional_email_verification(user, business=self.business)
        )
        resent = queue_professional_email_verification(user, business=self.business)

        self.assertEqual(first.status, OutboundEmail.Status.SENT)
        self.assertEqual(resent.pk, first.pk)
        self.assertEqual(resent.status, OutboundEmail.Status.PENDING)
        self.assertIsNone(resent.sent_at)
        self.assertEqual(queue_and_dispatch(resent).status, OutboundEmail.Status.SENT)
        self.assertEqual(len(mail.outbox), 2)

    def test_management_command_processes_due_email(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600999003",
            full_name="Profesional por procesar",
            email="procesar@example.test",
            password=None,
            is_active=False,
            email_verification_required=True,
        )
        BusinessMembership.objects.create(business=self.business, user=user)
        email = queue_professional_activation(user, business=self.business)
        output = StringIO()

        call_command("process_outbound_emails", "--limit", "0", stdout=output)

        email.refresh_from_db()
        self.assertEqual(email.status, OutboundEmail.Status.SENT)
        self.assertIn("Procesados: 1. Enviados: 1", output.getvalue())

    def test_dispatch_retries_and_eventually_marks_a_failure(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600999004",
            full_name="Profesional sin proveedor",
            email="fallo@example.test",
            password=None,
            is_active=False,
            email_verification_required=True,
        )
        BusinessMembership.objects.create(business=self.business, user=user)
        email = queue_professional_activation(user, business=self.business)

        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            for expected_attempt in range(1, 4):
                email.scheduled_for = timezone.now() - timedelta(seconds=1)
                email.save(update_fields=["scheduled_for", "updated_at"])
                email = queue_and_dispatch(email)
                self.assertEqual(email.attempts, expected_attempt)

        self.assertEqual(email.status, OutboundEmail.Status.FAILED)
        self.assertIn("no está activado", email.last_error)

    def test_dispatch_skips_future_and_cancelled_email(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600999005",
            full_name="Profesional futura",
            email="futura@example.test",
            password=None,
            is_active=False,
            email_verification_required=True,
        )
        email = queue_professional_activation(user, business=self.business)
        email.scheduled_for = timezone.now() + timedelta(hours=1)
        email.save(update_fields=["scheduled_for", "updated_at"])

        self.assertEqual(queue_and_dispatch(email).status, OutboundEmail.Status.PENDING)
        email.status = OutboundEmail.Status.CANCELLED
        email.save(update_fields=["status", "updated_at"])
        self.assertEqual(queue_and_dispatch(email).status, OutboundEmail.Status.CANCELLED)
        self.assertEqual(len(mail.outbox), 0)

    def test_appointment_confirmation_and_reminder_are_delivered(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente avisado",
            phone="600999103",
            email="avisado@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        line = WorkLine.objects.create(business=self.business, line_number=1, name="Línea 1")
        starts_at = timezone.now() + timedelta(days=2)
        appointment = Appointment.objects.create(
            business=self.business,
            business_client=access.business_client,
            work_line=line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            requested_by_client_access=access,
            service_summary_snapshot="Corte",
        )
        confirmation, reminder = queue_appointment_emails(appointment)
        reminder.scheduled_for = timezone.now() - timedelta(seconds=1)
        reminder.save(update_fields=["scheduled_for", "updated_at"])

        self.assertEqual(queue_and_dispatch(confirmation).status, OutboundEmail.Status.SENT)
        self.assertEqual(queue_and_dispatch(reminder).status, OutboundEmail.Status.SENT)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("está confirmada", mail.outbox[0].body)
        self.assertIn("mañana tienes cita", mail.outbox[1].body)

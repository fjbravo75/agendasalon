import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment, WorkLine
from apps.businesses.models import Business, BusinessMembership
from apps.customers.services import register_client_access
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    _claim_outbound_email,
    _finish_claim_as_accepted,
    cancel_appointment_emails,
    client_password_reset_url,
    client_verification_url,
    dispatch_due_emails,
    dispatch_outbound_email,
    queue_and_dispatch,
    queue_appointment_emails,
    queue_client_email_verification,
    queue_client_password_reset,
    queue_professional_activation,
    queue_professional_email_verification,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True,
    AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=False,
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

    def _queue_professional_activation(self, index):
        user = get_user_model().objects.create_user(
            normalized_phone=f"+34610{index:06d}",
            full_name=f"Profesional de cola {index}",
            email=f"cola-{index}@example.test",
            password=None,
            is_active=False,
            email_verification_required=True,
        )
        BusinessMembership.objects.create(business=self.business, user=user)
        return queue_professional_activation(user, business=self.business)

    def test_sent_status_describes_provider_acceptance_without_claiming_delivery(self):
        delivery = OutboundEmail(status=OutboundEmail.Status.SENT)

        self.assertEqual(
            delivery.operational_status_label,
            "Aceptado por el servicio de correo",
        )
        self.assertIn("no confirma", delivery.operational_status_message)
        self.assertIn("haya llegado a su bandeja", delivery.operational_status_message)

    def test_in_flight_cancellation_has_a_truthful_operational_message(self):
        delivery = OutboundEmail(
            status=OutboundEmail.Status.PROCESSING,
            cancellation_requested_at=timezone.now(),
        )

        self.assertIn("La cita está cancelada", delivery.operational_status_message)
        self.assertIn("todavía puede aceptarlo", delivery.operational_status_message)

        delivery.status = OutboundEmail.Status.SENT
        self.assertIn("aceptó el aviso", delivery.operational_status_message)
        self.assertIn("todavía puede llegar", delivery.operational_status_message)

    def test_public_operational_error_messages_never_expose_the_raw_error(self):
        technical_error = (
            "HTTPSConnectionPool(host='smtp.private.example', port=443): "
            "Max retries exceeded; password=SuperSecreta"
        )
        pending = OutboundEmail(
            status=OutboundEmail.Status.PENDING,
            attempts=1,
            last_error=technical_error,
        )
        failed = OutboundEmail(
            status=OutboundEmail.Status.FAILED,
            attempts=3,
            last_error=technical_error,
        )

        self.assertIn("Se volverá a intentar automáticamente", pending.operational_status_message)
        self.assertIn("tras agotar los intentos", failed.operational_status_message)
        self.assertIn("administrador de AgendaSalon", failed.operational_status_message)
        self.assertNotIn("soporte", failed.operational_status_message)
        self.assertNotIn("smtp.private.example", pending.operational_status_message)
        self.assertNotIn("SuperSecreta", failed.operational_status_message)

    def test_professional_appointment_detail_hides_raw_delivery_error(self):
        professional = get_user_model().objects.create_user(
            normalized_phone="+34600999111",
            full_name="Profesional que revisa avisos",
            email="profesional-avisos@example.test",
            password="MiClavePersonal2026!",
            is_active=True,
        )
        BusinessMembership.objects.create(business=self.business, user=professional)
        access = register_client_access(
            business=self.business,
            full_name="Cliente con fallo de correo",
            phone="600999111",
            email="fallo-cliente@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        line = WorkLine.objects.create(business=self.business, line_number=1, name="Linea 1")
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
        technical_error = (
            "HTTPSConnectionPool(host='smtp.private.example', port=443): "
            "Max retries exceeded; password=SuperSecreta"
        )
        delivery = OutboundEmail.objects.create(
            kind=OutboundEmail.Kind.APPOINTMENT_CONFIRMATION,
            status=OutboundEmail.Status.FAILED,
            business=self.business,
            client_access=access,
            appointment=appointment,
            recipient_email=access.email,
            deduplication_key="professional-detail-raw-error",
            scheduled_for=timezone.now() + timedelta(minutes=5),
            attempts=3,
            last_error=technical_error,
        )
        self.client.force_login(professional)

        response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tras agotar los intentos")
        self.assertContains(response, "sin más intentos")
        self.assertNotContains(response, "smtp.private.example")
        self.assertNotContains(response, "SuperSecreta")
        self.assertNotContains(response, "HTTPSConnectionPool")
        self.assertNotContains(response, "próximo intento")
        self.assertNotContains(response, "previsto para")

        accepted_at = timezone.now()
        OutboundEmail.objects.filter(pk=delivery.pk).update(
            status=OutboundEmail.Status.SENT,
            sent_at=accepted_at,
            last_error="",
        )
        response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.pk])
        )
        self.assertContains(response, "aceptado por el servicio de correo el")

        OutboundEmail.objects.filter(pk=delivery.pk).update(
            status=OutboundEmail.Status.PENDING,
            sent_at=None,
            attempts=1,
            last_error=technical_error,
            scheduled_for=timezone.now() + timedelta(minutes=5),
        )
        response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.pk])
        )
        self.assertContains(response, "próximo intento el")
        self.assertContains(response, "Se volverá a intentar automáticamente")
        self.assertNotContains(response, "smtp.private.example")

        OutboundEmail.objects.filter(pk=delivery.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            lease_token=uuid.uuid4(),
            lease_expires_at=timezone.now() + timedelta(minutes=2),
        )
        response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.pk])
        )
        self.assertContains(response, "procesamiento en curso")
        self.assertContains(response, "se está procesando en este momento")
        self.assertNotContains(response, "próximo intento el")

        OutboundEmail.objects.filter(pk=delivery.pk).update(
            status=OutboundEmail.Status.CANCELLED,
            lease_token=None,
            lease_expires_at=None,
        )
        response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[appointment.pk])
        )
        self.assertContains(response, "cancelado · actualizado el")
        self.assertContains(response, "no volverá a intentarse")
        self.assertNotContains(response, "próximo intento el")

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
        self.assertEqual(response.status_code, 200)
        access.refresh_from_db()
        self.assertIsNone(access.email_verified_at)
        self.assertNotIn("business_client_access_id", self.client.session)

        response = self.client.post(
            verify_path,
            {
                "password": "ClaveElegidaTrasCorreo2026!",
                "password_confirm": "ClaveElegidaTrasCorreo2026!",
            },
        )
        self.assertEqual(response.status_code, 302)
        access.refresh_from_db()
        self.assertIsNotNone(access.email_verified_at)
        self.assertFalse(access.check_password("ClienteDemo2026!"))
        self.assertTrue(access.check_password("ClaveElegidaTrasCorreo2026!"))
        self.assertEqual(self.client.session["business_client_access_id"], access.pk)

        replay = self.client_class().get(verify_path)
        self.assertEqual(replay.status_code, 410)

    def test_client_password_reset_email_contains_a_scoped_one_hour_link(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente Recuperación",
            phone="600999104",
            email="recuperacion@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )

        delivery = queue_and_dispatch(queue_client_password_reset(access))

        self.assertEqual(delivery.status, OutboundEmail.Status.SENT)
        self.assertEqual(delivery.kind, OutboundEmail.Kind.CLIENT_PASSWORD_RESET)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("60 minutos", mail.outbox[0].body)
        self.assertIn("solo puede usarse una vez", mail.outbox[0].body)
        reset_path = urlparse(client_password_reset_url(access)).path
        self.assertIn(reset_path, mail.outbox[0].body)
        response = self.client.get(reset_path)
        self.assertEqual(response.status_code, 200)
        access.refresh_from_db()
        self.assertTrue(access.check_password("ClienteDemo2026!"))

    def test_queued_password_reset_is_cancelled_if_password_changes_before_delivery(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente con cambio previo",
            phone="600999105",
            email="cambio@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        queued = queue_client_password_reset(access)
        access.set_password("CambioPrevioCliente2026!")
        access.save(update_fields=["password_hash", "updated_at"])

        delivery = queue_and_dispatch(queued)

        self.assertEqual(delivery.status, OutboundEmail.Status.CANCELLED)
        self.assertEqual(len(mail.outbox), 0)

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

    def test_cancellation_classifies_one_locked_snapshot_of_pending_and_processing(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente con avisos en estados distintos",
            phone="600999113",
            email="estados-distintos@example.test",
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
            service_summary_snapshot="Corte",
        )
        confirmation, reminder = queue_appointment_emails(appointment)
        claim, _ = _claim_outbound_email(email_id=confirmation.pk)
        self.assertIsNotNone(claim)

        cancelled = cancel_appointment_emails(appointment)

        confirmation.refresh_from_db()
        reminder.refresh_from_db()
        self.assertEqual(cancelled, 2)
        self.assertEqual(confirmation.status, OutboundEmail.Status.PROCESSING)
        self.assertEqual(confirmation.lease_token, claim.lease_token)
        self.assertIsNotNone(confirmation.cancellation_requested_at)
        self.assertEqual(reminder.status, OutboundEmail.Status.CANCELLED)
        self.assertIsNotNone(reminder.cancellation_requested_at)

    def test_requeue_refreshes_pending_appointment_recipient_without_bypassing_backoff(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente con correo actualizado",
            phone="600999110",
            email="anterior@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        line = WorkLine.objects.create(business=self.business, line_number=1, name="Linea 1")
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
        confirmation, _ = queue_appointment_emails(appointment)
        original_reference = confirmation.delivery_reference
        retry_at = timezone.now() + timedelta(minutes=5)
        OutboundEmail.objects.filter(pk=confirmation.pk).update(
            attempts=1,
            last_error="Fallo temporal",
            scheduled_for=retry_at,
        )
        access.email = "actualizado@example.test"
        access.email_normalized = "actualizado@example.test"
        access.save(update_fields=["email", "email_normalized", "updated_at"])

        refreshed, _ = queue_appointment_emails(appointment)

        self.assertEqual(refreshed.pk, confirmation.pk)
        self.assertEqual(refreshed.recipient_email, "actualizado@example.test")
        self.assertEqual(refreshed.attempts, 1)
        self.assertEqual(refreshed.last_error, "Fallo temporal")
        self.assertEqual(refreshed.scheduled_for, retry_at)
        self.assertEqual(refreshed.delivery_reference, original_reference)

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
        self.assertIn("Procesados: 1", output.getvalue())
        self.assertIn("Aceptados por el servicio de correo: 1", output.getvalue())

    def test_disabled_dispatch_does_not_consume_retries(self):
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
        delivery_reference = email.delivery_reference
        original_scheduled_for = email.scheduled_for

        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            for _ in range(3):
                email = queue_and_dispatch(email)
                self.assertEqual(email.attempts, 0)

        self.assertEqual(email.status, OutboundEmail.Status.PENDING)
        self.assertEqual(email.delivery_reference, delivery_reference)
        self.assertEqual(email.scheduled_for, original_scheduled_for)
        self.assertEqual(email.last_error, "")

    def test_demo_refresh_suppression_never_calls_the_email_backend(self):
        email = self._queue_professional_activation(104)

        with (
            override_settings(AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=True),
            patch("apps.notifications.services.EmailMultiAlternatives.send") as send,
        ):
            delivery = queue_and_dispatch(email)

        self.assertEqual(delivery.status, OutboundEmail.Status.PENDING)
        self.assertEqual(delivery.attempts, 0)
        self.assertEqual(delivery.last_error, "")
        self.assertEqual(len(mail.outbox), 0)
        send.assert_not_called()

    def test_disabled_transactional_email_never_calls_the_email_backend(self):
        email = self._queue_professional_activation(105)

        with (
            override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False),
            patch("apps.notifications.services.EmailMultiAlternatives.send") as send,
        ):
            delivery = queue_and_dispatch(email)

        self.assertEqual(delivery.status, OutboundEmail.Status.PENDING)
        self.assertEqual(delivery.attempts, 0)
        self.assertEqual(delivery.last_error, "")
        send.assert_not_called()

    def test_blocked_due_dispatcher_leaves_the_queue_intact(self):
        blocked_settings = (
            {"AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL": True},
            {"AGENDA_TRANSACTIONAL_EMAIL_ENABLED": False},
        )

        for index, setting_overrides in enumerate(blocked_settings, start=107):
            with self.subTest(setting_overrides=setting_overrides):
                email = self._queue_professional_activation(index)
                original_scheduled_for = email.scheduled_for

                with (
                    override_settings(**setting_overrides),
                    patch(
                        "apps.notifications.services.EmailMultiAlternatives.send"
                    ) as send,
                ):
                    delivered = dispatch_due_emails(limit=100)

                email.refresh_from_db()
                self.assertEqual(delivered, [])
                self.assertEqual(email.status, OutboundEmail.Status.PENDING)
                self.assertEqual(email.attempts, 0)
                self.assertEqual(email.last_error, "")
                self.assertEqual(email.scheduled_for, original_scheduled_for)
                send.assert_not_called()

    def test_normal_dispatch_reaches_email_backend_when_suppression_is_off(self):
        email = self._queue_professional_activation(106)

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            autospec=True,
            return_value=1,
        ) as send:
            delivery = queue_and_dispatch(email)

        self.assertEqual(delivery.status, OutboundEmail.Status.SENT)
        send.assert_called_once()

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

    def test_outbound_email_admin_is_strictly_read_only(self):
        model_admin = admin.site._registry[OutboundEmail]

        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))

    def test_outbound_email_admin_uses_truthful_operational_status(self):
        model_admin = admin.site._registry[OutboundEmail]
        accepted_at = timezone.now()
        delivery = OutboundEmail(
            status=OutboundEmail.Status.SENT,
            sent_at=accepted_at,
        )

        self.assertIn("operational_status", model_admin.list_display)
        self.assertNotIn("status", model_admin.list_display)
        self.assertIn("accepted_at", model_admin.list_display)
        self.assertNotIn("sent_at", model_admin.list_display)
        self.assertIn("operational_status", model_admin.fields)
        self.assertNotIn("status", model_admin.fields)
        self.assertIn("accepted_at", model_admin.fields)
        self.assertNotIn("sent_at", model_admin.fields)
        self.assertIn("technical_last_error", model_admin.fields)
        self.assertNotIn("last_error", model_admin.fields)
        self.assertEqual(
            model_admin.operational_status(delivery),
            "Aceptado por el servicio de correo",
        )
        self.assertEqual(model_admin.accepted_at(delivery), accepted_at)
        self.assertEqual(
            model_admin.accepted_at.short_description,
            "aceptado por el servicio de correo el",
        )
        delivery.last_error = "SMTP 451: detalle técnico"
        self.assertEqual(
            model_admin.technical_last_error(delivery),
            "SMTP 451: detalle técnico",
        )
        self.assertEqual(
            model_admin.technical_last_error.short_description,
            "detalle técnico del último error",
        )

    def test_outbound_email_admin_filter_uses_truthful_operational_status(self):
        model_admin = admin.site._registry[OutboundEmail]
        filter_class = model_admin.list_filter[1]
        status_filter = filter_class(
            RequestFactory().get("/admin/notifications/outboundemail/"),
            {},
            OutboundEmail,
            model_admin,
        )

        self.assertNotIn("status", model_admin.list_filter)
        self.assertEqual(
            dict(status_filter.lookup_choices)[OutboundEmail.Status.SENT],
            "Aceptado por el servicio de correo",
        )
        self.assertEqual(model_admin.autocomplete_fields, ())

    def test_outbound_email_admin_technical_fields_have_clear_spanish_labels(self):
        expected_labels = {
            "delivery_reference": "identificador del aviso",
            "lease_token": "identificador del procesamiento",
            "lease_expires_at": "procesamiento reservado hasta",
            "cancellation_requested_at": "cancelación solicitada el",
        }

        for field_name, expected_label in expected_labels.items():
            with self.subTest(field=field_name):
                self.assertEqual(
                    OutboundEmail._meta.get_field(field_name).verbose_name,
                    expected_label,
                )

    def test_active_lease_is_not_claimed_or_sent_again(self):
        email = self._queue_professional_activation(201)
        lease_token = uuid.uuid4()
        OutboundEmail.objects.filter(pk=email.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            attempts=1,
            lease_token=lease_token,
            lease_expires_at=timezone.now() + timedelta(minutes=2),
        )

        with patch("apps.notifications.services.EmailMultiAlternatives.send") as send:
            delivery = dispatch_outbound_email(email.pk)

        self.assertEqual(delivery.status, OutboundEmail.Status.PROCESSING)
        self.assertEqual(delivery.attempts, 1)
        self.assertEqual(delivery.lease_token, lease_token)
        send.assert_not_called()

    def test_expired_processing_lease_is_recovered(self):
        email = self._queue_professional_activation(202)
        delivery_reference = email.delivery_reference
        OutboundEmail.objects.filter(pk=email.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            attempts=1,
            lease_token=uuid.uuid4(),
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )

        delivery = dispatch_outbound_email(email.pk)

        self.assertEqual(delivery.status, OutboundEmail.Status.SENT)
        self.assertEqual(delivery.attempts, 2)
        self.assertEqual(delivery.delivery_reference, delivery_reference)
        self.assertIsNone(delivery.lease_token)
        self.assertIsNone(delivery.lease_expires_at)
        self.assertEqual(len(mail.outbox), 1)

    def test_expired_lease_at_attempt_limit_becomes_failed_without_sending(self):
        email = self._queue_professional_activation(203)
        OutboundEmail.objects.filter(pk=email.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            attempts=3,
            lease_token=uuid.uuid4(),
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )

        with patch("apps.notifications.services.EmailMultiAlternatives.send") as send:
            delivery = dispatch_outbound_email(email.pk)

        self.assertEqual(delivery.status, OutboundEmail.Status.FAILED)
        self.assertEqual(delivery.attempts, 3)
        self.assertIsNone(delivery.lease_token)
        self.assertIsNone(delivery.lease_expires_at)
        send.assert_not_called()

    def test_backend_zero_acceptance_is_retried_and_releases_lease(self):
        email = self._queue_professional_activation(204)

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            return_value=0,
        ):
            delivery = dispatch_outbound_email(email.pk)

        self.assertEqual(delivery.status, OutboundEmail.Status.PENDING)
        self.assertEqual(delivery.attempts, 1)
        self.assertIsNone(delivery.lease_token)
        self.assertIsNone(delivery.lease_expires_at)
        self.assertIn("no confirmó la aceptación", delivery.last_error)

    def test_delivery_reference_is_sent_in_stable_headers(self):
        email = self._queue_professional_activation(205)
        headers = {}

        def accept_message(message, *, fail_silently=False):
            headers.update(message.extra_headers)
            return 1

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            autospec=True,
            side_effect=accept_message,
        ):
            delivery = dispatch_outbound_email(email.pk)

        self.assertEqual(delivery.status, OutboundEmail.Status.SENT)
        self.assertEqual(
            headers["X-AgendaSalon-Delivery-Reference"],
            str(email.delivery_reference),
        )
        self.assertEqual(
            headers["Message-ID"],
            f"<{email.delivery_reference}@example.test>",
        )

    def test_lease_is_renewed_immediately_before_smtp(self):
        email = self._queue_professional_activation(211)
        remaining_lease = []

        def accept_message(message, *, fail_silently=False):
            processing = OutboundEmail.objects.get(pk=email.pk)
            remaining_lease.append(processing.lease_expires_at - timezone.now())
            return 1

        with (
            patch(
                "apps.notifications.services._lease_duration",
                side_effect=[timedelta(seconds=5), timedelta(seconds=120)],
            ),
            patch(
                "apps.notifications.services.EmailMultiAlternatives.send",
                autospec=True,
                side_effect=accept_message,
            ),
        ):
            delivery = dispatch_outbound_email(email.pk)

        self.assertEqual(delivery.status, OutboundEmail.Status.SENT)
        self.assertEqual(len(remaining_lease), 1)
        self.assertGreater(remaining_lease[0], timedelta(seconds=100))

    def test_resend_does_not_steal_an_active_lease(self):
        email = self._queue_professional_activation(206)
        lease_token = uuid.uuid4()
        OutboundEmail.objects.filter(pk=email.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            attempts=1,
            lease_token=lease_token,
            lease_expires_at=timezone.now() + timedelta(minutes=2),
        )

        resent = queue_professional_activation(
            email.recipient_user,
            business=self.business,
        )

        self.assertEqual(resent.status, OutboundEmail.Status.PROCESSING)
        self.assertEqual(resent.attempts, 1)
        self.assertEqual(resent.lease_token, lease_token)
        self.assertEqual(resent.delivery_reference, email.delivery_reference)

    def test_explicit_resend_rotates_delivery_reference(self):
        email = self._queue_professional_activation(207)
        sent = dispatch_outbound_email(email.pk)
        previous_reference = sent.delivery_reference

        resent = queue_professional_activation(
            email.recipient_user,
            business=self.business,
        )

        self.assertEqual(resent.status, OutboundEmail.Status.PENDING)
        self.assertEqual(resent.attempts, 0)
        self.assertNotEqual(resent.delivery_reference, previous_reference)

    def test_stale_claim_cannot_finish_after_the_row_is_reclaimed(self):
        email = self._queue_professional_activation(210)
        first_claim, _ = _claim_outbound_email(email_id=email.pk)
        OutboundEmail.objects.filter(pk=email.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        second_claim, _ = _claim_outbound_email(email_id=email.pk)

        stale_result = _finish_claim_as_accepted(first_claim)

        self.assertEqual(stale_result.status, OutboundEmail.Status.PROCESSING)
        self.assertEqual(stale_result.lease_token, second_claim.lease_token)
        self.assertEqual(stale_result.attempts, 2)

        accepted = _finish_claim_as_accepted(second_claim)
        self.assertEqual(accepted.status, OutboundEmail.Status.SENT)

    def test_due_dispatch_skips_active_lease_and_processes_next_email(self):
        active = self._queue_professional_activation(208)
        next_email = self._queue_professional_activation(209)
        OutboundEmail.objects.filter(pk=active.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            attempts=1,
            lease_token=uuid.uuid4(),
            lease_expires_at=timezone.now() + timedelta(minutes=2),
            scheduled_for=timezone.now() - timedelta(minutes=2),
        )
        OutboundEmail.objects.filter(pk=next_email.pk).update(
            scheduled_for=timezone.now() - timedelta(minutes=1),
        )

        delivered = dispatch_due_emails(limit=1)

        self.assertEqual([email.pk for email in delivered], [next_email.pk])
        active.refresh_from_db()
        self.assertEqual(active.status, OutboundEmail.Status.PROCESSING)
        self.assertEqual(active.attempts, 1)

    def test_smtp_acceptance_remains_truthful_when_cancellation_arrives_in_flight(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente cancelado durante el envio",
            phone="600999109",
            email="cancelacion@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        line = WorkLine.objects.create(business=self.business, line_number=1, name="Linea 1")
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
        confirmation, _ = queue_appointment_emails(appointment)

        def accept_after_cancellation(message, *, fail_silently=False):
            cancel_appointment_emails(appointment)
            return 1

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            autospec=True,
            side_effect=accept_after_cancellation,
        ):
            delivery = dispatch_outbound_email(confirmation.pk)

        self.assertEqual(delivery.status, OutboundEmail.Status.SENT)
        self.assertIsNotNone(delivery.sent_at)
        self.assertIsNotNone(delivery.cancellation_requested_at)
        self.assertIsNone(delivery.lease_token)
        self.assertIsNone(delivery.lease_expires_at)

    def test_smtp_failure_after_in_flight_cancellation_is_not_retried(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente cancelado durante un fallo",
            phone="600999111",
            email="cancelacion-fallida@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        line = WorkLine.objects.create(business=self.business, line_number=1, name="Linea 2")
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
            service_summary_snapshot="Peinado",
        )
        confirmation, _ = queue_appointment_emails(appointment)

        def fail_after_cancellation(message, *, fail_silently=False):
            cancel_appointment_emails(appointment)
            raise RuntimeError("SMTP interrumpido después de cancelar")

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            autospec=True,
            side_effect=fail_after_cancellation,
        ):
            delivery = dispatch_outbound_email(confirmation.pk)

        self.assertEqual(delivery.status, OutboundEmail.Status.CANCELLED)
        self.assertEqual(delivery.attempts, 1)
        self.assertIsNotNone(delivery.cancellation_requested_at)
        self.assertIsNone(delivery.lease_token)
        self.assertIsNone(delivery.lease_expires_at)
        self.assertIn("cancelada durante el envío", delivery.last_error)

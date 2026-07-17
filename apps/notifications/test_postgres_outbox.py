from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from apps.booking.models import Appointment, WorkLine
from apps.booking.services import cancel_appointment
from apps.businesses.models import Business, BusinessMembership
from apps.customers.services import register_client_access
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    cancel_appointment_emails,
    dispatch_due_emails,
    dispatch_outbound_email,
    queue_appointment_emails,
    queue_professional_activation,
)


@skipUnlessDBFeature("has_select_for_update_skip_locked")
@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True,
    AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS=120,
    AGENDA_PLATFORM_WEBSITE="https://example.test",
    DEFAULT_FROM_EMAIL="AgendaSalon <agenda@example.test>",
)
class PostgreSQLOutboxConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluqueria de concurrencia",
            slug="peluqueria-concurrencia",
            is_active=True,
            public_booking_enabled=True,
        )

    def _queue_professional(self, index):
        user = get_user_model().objects.create_user(
            normalized_phone=f"+34620{index:06d}",
            full_name=f"Profesional concurrente {index}",
            email=f"concurrente-{index}@example.test",
            password=None,
            is_active=False,
            email_verification_required=True,
        )
        BusinessMembership.objects.create(business=self.business, user=user)
        return queue_professional_activation(user, business=self.business)

    @staticmethod
    def _dispatch_in_connection(email_id):
        connections.close_all()
        try:
            return dispatch_outbound_email(email_id).status
        finally:
            connections.close_all()

    @staticmethod
    def _dispatch_due_in_connection():
        connections.close_all()
        try:
            return [email.pk for email in dispatch_due_emails(limit=1)]
        finally:
            connections.close_all()

    @staticmethod
    def _cancel_appointment_emails_in_connection(appointment_id):
        connections.close_all()
        try:
            appointment = Appointment.objects.get(pk=appointment_id)
            return cancel_appointment_emails(appointment)
        finally:
            connections.close_all()

    def test_two_workers_send_the_same_row_only_once(self):
        email = self._queue_professional(1)
        send_started = Event()
        release_send = Event()
        calls_lock = Lock()
        send_calls = 0

        def controlled_send(message, *, fail_silently=False):
            nonlocal send_calls
            with calls_lock:
                send_calls += 1
            send_started.set()
            if not release_send.wait(timeout=5):
                raise TimeoutError("La prueba no libero el backend de correo.")
            return 1

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            autospec=True,
            side_effect=controlled_send,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(self._dispatch_in_connection, email.pk)
                self.assertTrue(send_started.wait(timeout=5))
                second = executor.submit(self._dispatch_in_connection, email.pk)
                second_status = second.result(timeout=5)
                release_send.set()
                first_status = first.result(timeout=5)

        email.refresh_from_db()
        self.assertEqual(send_calls, 1)
        self.assertEqual(second_status, OutboundEmail.Status.PROCESSING)
        self.assertEqual(first_status, OutboundEmail.Status.SENT)
        self.assertEqual(email.status, OutboundEmail.Status.SENT)
        self.assertEqual(email.attempts, 1)
        self.assertIsNone(email.lease_token)
        self.assertIsNone(email.lease_expires_at)

    @override_settings(AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS=1)
    def test_heartbeat_keeps_the_claim_past_its_original_smtp_lease(self):
        email = self._queue_professional(4)
        send_started = Event()
        release_send = Event()
        calls_lock = Lock()
        send_calls = 0

        def controlled_send(message, *, fail_silently=False):
            nonlocal send_calls
            with calls_lock:
                send_calls += 1
            send_started.set()
            if not release_send.wait(timeout=5):
                raise TimeoutError("La prueba no liberó el backend de correo.")
            return 1

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            autospec=True,
            side_effect=controlled_send,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(self._dispatch_in_connection, email.pk)
                self.assertTrue(send_started.wait(timeout=5))
                email.refresh_from_db()
                original_expiry = email.lease_expires_at
                while timezone.now() <= original_expiry + timedelta(milliseconds=150):
                    Event().wait(0.05)

                email.refresh_from_db()
                self.assertGreater(email.lease_expires_at, timezone.now())
                second = executor.submit(self._dispatch_in_connection, email.pk)
                try:
                    second_status = second.result(timeout=3)
                finally:
                    release_send.set()
                first_status = first.result(timeout=5)

        email.refresh_from_db()
        self.assertEqual(send_calls, 1)
        self.assertEqual(second_status, OutboundEmail.Status.PROCESSING)
        self.assertEqual(first_status, OutboundEmail.Status.SENT)
        self.assertEqual(email.status, OutboundEmail.Status.SENT)
        self.assertEqual(email.attempts, 1)

    def test_workers_claim_different_due_rows_without_global_serialization(self):
        first_email = self._queue_professional(2)
        second_email = self._queue_professional(3)
        delivery_barrier = Barrier(2, timeout=5)
        calls_lock = Lock()
        send_calls = 0

        def synchronized_send(message, *, fail_silently=False):
            nonlocal send_calls
            with calls_lock:
                send_calls += 1
            delivery_barrier.wait()
            return 1

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            autospec=True,
            side_effect=synchronized_send,
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: self._dispatch_due_in_connection(), range(2)))

        claimed_ids = [email_id for result in results for email_id in result]
        self.assertEqual(send_calls, 2)
        self.assertCountEqual(claimed_ids, [first_email.pk, second_email.pk])
        self.assertFalse(
            OutboundEmail.objects.exclude(status=OutboundEmail.Status.SENT).exists()
        )

    def test_cancellation_during_smtp_preserves_the_truthful_acceptance(self):
        professional = get_user_model().objects.create_user(
            normalized_phone="+34620999999",
            full_name="Profesional que cancela",
            email="profesional-cancelacion@example.test",
            password="ClavePrueba2026!",
            is_active=True,
            email_verification_required=False,
            email_verified_at=timezone.now(),
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=professional,
            role=BusinessMembership.Role.PROFESSIONAL_ADMIN,
        )
        access = register_client_access(
            business=self.business,
            full_name="Cliente con aviso en curso",
            phone="600999112",
            email="cliente-aviso-en-curso@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        line = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Línea principal",
        )
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
        send_started = Event()
        release_send = Event()

        def controlled_send(message, *, fail_silently=False):
            send_started.set()
            if not release_send.wait(timeout=5):
                raise TimeoutError("La prueba no liberó el backend de correo.")
            return 1

        with patch(
            "apps.notifications.services.EmailMultiAlternatives.send",
            autospec=True,
            side_effect=controlled_send,
        ):
            with ThreadPoolExecutor(max_workers=1) as executor:
                delivery_future = executor.submit(
                    self._dispatch_in_connection,
                    confirmation.pk,
                )
                self.assertTrue(send_started.wait(timeout=5))
                cancel_appointment(
                    appointment,
                    cancelled_by=professional,
                    reason="Cambio de planes",
                )
                release_send.set()
                delivery_status = delivery_future.result(timeout=5)

        appointment.refresh_from_db()
        confirmation.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)
        self.assertEqual(delivery_status, OutboundEmail.Status.SENT)
        self.assertEqual(confirmation.status, OutboundEmail.Status.SENT)
        self.assertIsNotNone(confirmation.sent_at)
        self.assertIsNotNone(confirmation.cancellation_requested_at)
        self.assertIsNone(confirmation.lease_token)
        self.assertIsNone(confirmation.lease_expires_at)

    def test_cancellation_locks_all_rows_before_a_worker_can_claim_one(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente con cancelación coordinada",
            phone="600999114",
            email="cancelacion-coordinada@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        line = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Línea coordinada",
        )
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
        rows_locked = Event()
        release_cancellation = Event()

        from apps.notifications import services as notification_services

        original_apply = notification_services._apply_locked_appointment_email_cancellation

        def pause_after_lock(emails, *, now):
            rows_locked.set()
            if not release_cancellation.wait(timeout=5):
                raise TimeoutError("La prueba no liberó la cancelación.")
            return original_apply(emails, now=now)

        with (
            patch(
                "apps.notifications.services._apply_locked_appointment_email_cancellation",
                autospec=True,
                side_effect=pause_after_lock,
            ),
            patch("apps.notifications.services.EmailMultiAlternatives.send") as send,
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            cancellation = executor.submit(
                self._cancel_appointment_emails_in_connection,
                appointment.pk,
            )
            self.assertTrue(rows_locked.wait(timeout=5))
            worker = executor.submit(self._dispatch_in_connection, confirmation.pk)
            try:
                worker_status = worker.result(timeout=3)
            finally:
                release_cancellation.set()
            cancelled_count = cancellation.result(timeout=5)

        confirmation.refresh_from_db()
        reminder.refresh_from_db()
        self.assertEqual(worker_status, OutboundEmail.Status.PENDING)
        send.assert_not_called()
        self.assertEqual(cancelled_count, 2)
        self.assertEqual(confirmation.status, OutboundEmail.Status.CANCELLED)
        self.assertEqual(reminder.status, OutboundEmail.Status.CANCELLED)
        self.assertFalse(
            OutboundEmail.objects.filter(
                appointment=appointment,
                status=OutboundEmail.Status.PROCESSING,
            ).exists()
        )

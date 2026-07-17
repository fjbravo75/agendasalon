from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta
from threading import Barrier, Event
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connections
from django.test import Client, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.booking import services as booking_services
from apps.booking import views as booking_views
from apps.booking.models import (
    Appointment,
    AvailabilityRule,
    BusinessCalendarSettings,
    BusinessClosure,
    Service,
    WorkLine,
)
from apps.booking.services import (
    AppointmentDraft,
    close_appointments,
    complete_appointment,
    confirm_appointment,
    mark_appointment_no_show,
)
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.customers.models import BusinessClient, BusinessClientAccess
from apps.customers.services import (
    CLIENT_ACCESS_LAST_SEEN_SESSION_KEY,
    CLIENT_ACCESS_PASSWORD_SESSION_KEY,
    CLIENT_ACCESS_SESSION_KEY,
    client_password_fingerprint,
)


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLAppointmentConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(
            normalized_phone="+34600111999",
            password="test-pass",
            full_name="Profesional de prueba",
        )
        self.business = Business.objects.create(
            commercial_name="Salón de concurrencia",
            slug="salon-concurrencia",
        )
        self.client_file = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente de prueba",
            phone="600111999",
        )
        self.work_line = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Línea 1",
        )
        starts_at = timezone.now() - timedelta(hours=2)
        self.appointment = Appointment.objects.create(
            business=self.business,
            business_client=self.client_file,
            work_line=self.work_line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            total_duration_minutes=60,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.user,
        )

    def test_simultaneous_outcomes_commit_only_one_final_state(self):
        barrier = Barrier(2)

        def apply_outcome(outcome):
            connections.close_all()
            appointment = Appointment.objects.get(pk=self.appointment.pk)
            user = User.objects.get(pk=self.user.pk)
            barrier.wait(timeout=5)
            try:
                if outcome == Appointment.Status.COMPLETED:
                    complete_appointment(appointment, completed_by=user)
                else:
                    mark_appointment_no_show(appointment, marked_by=user)
            except ValidationError:
                return "rejected"
            finally:
                connections.close_all()
            return "committed"

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    apply_outcome,
                    (Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW),
                )
            )

        self.assertCountEqual(results, ["committed", "rejected"])
        self.appointment.refresh_from_db()
        self.assertIn(
            self.appointment.status,
            {Appointment.Status.COMPLETED, Appointment.Status.NO_SHOW},
        )
        self.assertEqual(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_type="appointment",
                entity_id=str(self.appointment.pk),
                event_type__in={
                    BusinessActivityEvent.EventType.APPOINTMENT_COMPLETED,
                    BusinessActivityEvent.EventType.APPOINTMENT_NO_SHOW,
                },
            ).count(),
            1,
        )

    def test_bulk_outcome_prelocks_calendar_before_a_single_outcome(self):
        second_starts_at = self.appointment.starts_at - timedelta(hours=2)
        second_appointment = Appointment.objects.create(
            business=self.business,
            business_client=self.client_file,
            work_line=self.work_line,
            starts_at=second_starts_at,
            ends_at=second_starts_at + timedelta(hours=1),
            total_duration_minutes=60,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.user,
        )
        first_completed = Event()
        single_lock_started = Event()
        single_finished = Event()
        state = {"complete_calls": 0, "single_finished_before_bulk": None}
        real_complete_appointment = complete_appointment
        real_calendar_lock = booking_services.lock_business_calendar

        def held_complete_appointment(*args, **kwargs):
            result = real_complete_appointment(*args, **kwargs)
            state["complete_calls"] += 1
            if state["complete_calls"] == 1:
                first_completed.set()
                if not single_lock_started.wait(timeout=5):
                    raise AssertionError(
                        "La transición individual no intentó bloquear la agenda."
                    )
                state["single_finished_before_bulk"] = single_finished.wait(
                    timeout=0.25
                )
            return result

        def observed_calendar_lock(business):
            single_lock_started.set()
            return real_calendar_lock(business)

        def bulk_worker():
            connections.close_all()
            try:
                appointments = tuple(
                    Appointment.objects.filter(
                        pk__in=(self.appointment.pk, second_appointment.pk)
                    ).order_by("pk")
                )
                with patch(
                    "apps.booking.services.complete_appointment",
                    side_effect=held_complete_appointment,
                ):
                    return close_appointments(
                        appointments,
                        outcome=Appointment.Status.COMPLETED,
                        closed_by=User.objects.get(pk=self.user.pk),
                    )
            finally:
                connections.close_all()

        def single_worker():
            connections.close_all()
            try:
                if not first_completed.wait(timeout=5):
                    raise AssertionError("El cierre masivo no completó la primera cita.")
                try:
                    with patch(
                        "apps.booking.services.lock_business_calendar",
                        side_effect=observed_calendar_lock,
                    ):
                        mark_appointment_no_show(
                            Appointment.objects.get(pk=second_appointment.pk),
                            marked_by=User.objects.get(pk=self.user.pk),
                        )
                except ValidationError:
                    return "rejected"
                return "committed"
            finally:
                single_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            bulk_future = executor.submit(bulk_worker)
            single_future = executor.submit(single_worker)
            bulk_count = bulk_future.result(timeout=10)
            single_result = single_future.result(timeout=10)

        self.appointment.refresh_from_db()
        second_appointment.refresh_from_db()
        self.assertFalse(state["single_finished_before_bulk"])
        self.assertEqual(bulk_count, 2)
        self.assertEqual(single_result, "rejected")
        self.assertEqual(self.appointment.status, Appointment.Status.COMPLETED)
        self.assertEqual(second_appointment.status, Appointment.Status.COMPLETED)


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLCalendarMutationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(
            normalized_phone="+34600111888",
            password="test-pass",
            full_name="Profesional de agenda",
        )
        self.business = Business.objects.create(
            commercial_name="Salón agenda concurrente",
            slug="salon-agenda-concurrente",
        )
        BusinessMembership.objects.create(business=self.business, user=self.user)
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=15,
            apply_national_holidays=False,
        )
        self.client_file = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente concurrente",
            phone="600111888",
        )
        self.work_line = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Línea 1",
        )
        self.second_work_line = WorkLine.objects.create(
            business=self.business,
            line_number=2,
            name="Línea 2",
        )
        self.service = Service.objects.create(
            business=self.business,
            name="Servicio concurrente",
            duration_minutes=30,
            color_hex="#C56B5C",
        )
        self.target_date = timezone.localdate() + timedelta(days=14)
        self.availability_rule = AvailabilityRule.objects.create(
            business=self.business,
            weekday=self.target_date.weekday(),
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        self.starts_at = timezone.make_aware(
            datetime.combine(self.target_date, time(10, 0)),
            timezone.get_current_timezone(),
        )

    def test_confirm_and_pause_line_cannot_commit_incompatible_states(self):
        result = self._run_confirmation_against_mutation(
            reverse(
                "booking:professional_work_line_toggle",
                args=[self.work_line.pk],
            ),
            {},
        )

        self.assertEqual(result["confirmation"], "committed")
        self.assertEqual(result["mutation_status"], 302)
        self.assertFalse(result["mutation_finished_before_confirmation"])
        self.assertTrue(
            Appointment.objects.filter(
                business=self.business,
                work_line=self.work_line,
                status=Appointment.Status.CONFIRMED,
            ).exists()
        )
        self.work_line.refresh_from_db()
        self.assertTrue(self.work_line.is_active)

    def test_two_public_confirmations_keep_same_time_on_different_lines(self):
        second_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Segundo cliente concurrente",
            phone="600111887",
        )
        barrier = Barrier(2)

        def confirm_public(client_id):
            connections.close_all()
            try:
                barrier.wait(timeout=5)
                appointment = confirm_appointment(
                    AppointmentDraft(
                        business=Business.objects.get(pk=self.business.pk),
                        business_client=BusinessClient.objects.get(pk=client_id),
                        services=(Service.objects.get(pk=self.service.pk),),
                        work_line_id=self.work_line.pk,
                        starts_at=self.starts_at,
                        duration_minutes=30,
                        channel=Appointment.ManualChannel.PUBLIC_WEB,
                    ),
                    allow_line_reassignment=True,
                )
                return appointment.work_line_id
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            line_ids = list(
                executor.map(
                    confirm_public,
                    (self.client_file.pk, second_client.pk),
                )
            )

        self.assertCountEqual(
            line_ids,
            [self.work_line.pk, self.second_work_line.pk],
        )
        self.assertEqual(
            Appointment.objects.filter(
                business=self.business,
                starts_at=self.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
                status=Appointment.Status.CONFIRMED,
            ).count(),
            2,
        )

    def test_two_posts_from_the_same_public_draft_create_only_one_appointment(self):
        access = BusinessClientAccess(
            business=self.business,
            business_client=self.client_file,
            phone=self.client_file.phone,
            email="cliente-replay@example.test",
            email_verified_at=timezone.now(),
        )
        access.set_password("test-pass")
        access.save()

        browser = Client()
        session = browser.session
        session[CLIENT_ACCESS_SESSION_KEY] = access.pk
        session[CLIENT_ACCESS_LAST_SEEN_SESSION_KEY] = timezone.now().isoformat()
        session[CLIENT_ACCESS_PASSWORD_SESSION_KEY] = client_password_fingerprint(access)
        session.save()
        booking_url = reverse("public_booking", args=[self.business.slug])
        choose_response = browser.post(
            booking_url,
            {
                "action": "choose_slot",
                "services": [self.service.pk],
                "target_date": self.target_date.isoformat(),
                "selected_work_line_id": self.work_line.pk,
                "selected_starts_at": self.starts_at.isoformat(),
            },
        )
        self.assertEqual(choose_response.status_code, 302)
        shared_session_key = browser.cookies[settings.SESSION_COOKIE_NAME].value
        barrier = Barrier(2)

        def confirm_same_draft():
            connections.close_all()
            replay_browser = Client()
            replay_browser.cookies[settings.SESSION_COOKIE_NAME] = shared_session_key
            try:
                barrier.wait(timeout=5)
                response = replay_browser.post(
                    booking_url,
                    {"action": "confirm_booking"},
                )
                return response.status_code, response.get("Location", "")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda _: confirm_same_draft(), range(2)))

        receipt_url = reverse("public_booking_receipt", args=[self.business.slug])
        self.assertEqual(responses, [(302, receipt_url), (302, receipt_url)])
        appointments = Appointment.objects.filter(
            business=self.business,
            business_client=self.client_file,
            starts_at=self.starts_at,
            manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
        )
        self.assertEqual(appointments.count(), 1)
        appointment = appointments.get()
        self.assertIsNotNone(appointment.public_confirmation_reference)
        self.assertEqual(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                entity_type="appointment",
                entity_id=str(appointment.pk),
            ).count(),
            1,
        )
        self.assertGreater(appointment.outbound_emails.count(), 0)
        self.assertEqual(
            appointment.outbound_emails.values("deduplication_key").distinct().count(),
            appointment.outbound_emails.count(),
        )

    def test_confirm_and_pause_schedule_cannot_commit_incompatible_states(self):
        result = self._run_confirmation_against_mutation(
            reverse(
                "booking:professional_availability_toggle",
                args=[self.availability_rule.pk],
            ),
            {},
        )

        self.assertEqual(result["confirmation"], "committed")
        self.assertEqual(result["mutation_status"], 302)
        self.assertFalse(result["mutation_finished_before_confirmation"])
        self.availability_rule.refresh_from_db()
        self.assertTrue(self.availability_rule.is_active)

    def test_confirm_and_create_closure_cannot_commit_incompatible_states(self):
        result = self._run_confirmation_against_mutation(
            reverse("booking:professional_schedule"),
            {
                "form_kind": "closure",
                "closure-closure_type": BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
                "closure-date_from": self.target_date.isoformat(),
                "closure-date_to": self.target_date.isoformat(),
                "closure-start_time": "10:00",
                "closure-end_time": "11:00",
                "closure-work_line": "",
                "closure-internal_reason": "Bloqueo concurrente",
                "closure-is_active": "on",
            },
        )

        self.assertEqual(result["confirmation"], "committed")
        self.assertEqual(result["mutation_status"], 200)
        self.assertFalse(result["mutation_finished_before_confirmation"])
        self.assertIn(
            "No puedes aplicar este cierre porque se solapa",
            result["mutation_content"],
        )
        self.assertFalse(
            BusinessClosure.objects.filter(
                business=self.business,
                date_from=self.target_date,
                is_active=True,
            ).exists()
        )

    def _run_confirmation_against_mutation(self, mutation_url, mutation_data):
        availability_checked = Event()
        mutation_lock_started = Event()
        mutation_finished = Event()
        state = {"mutation_finished_before_confirmation": None}
        real_get_day_availability = booking_services.get_day_availability
        real_lock_business_calendar = booking_views.lock_business_calendar

        def held_get_day_availability(*args, **kwargs):
            result = real_get_day_availability(*args, **kwargs)
            availability_checked.set()
            if not mutation_lock_started.wait(timeout=5):
                raise AssertionError("La mutación no alcanzó el bloqueo de calendario.")
            state["mutation_finished_before_confirmation"] = mutation_finished.wait(
                timeout=0.25
            )
            return result

        def confirm_worker():
            connections.close_all()
            try:
                draft = AppointmentDraft(
                    business=Business.objects.get(pk=self.business.pk),
                    business_client=BusinessClient.objects.get(pk=self.client_file.pk),
                    services=(Service.objects.get(pk=self.service.pk),),
                    work_line_id=self.work_line.pk,
                    starts_at=self.starts_at,
                    duration_minutes=30,
                    channel=Appointment.ManualChannel.PHONE,
                    created_by=User.objects.get(pk=self.user.pk),
                )
                with patch(
                    "apps.booking.services.get_day_availability",
                    side_effect=held_get_day_availability,
                ):
                    confirm_appointment(draft)
            except ValidationError:
                return "rejected"
            finally:
                connections.close_all()
            return "committed"

        def observed_calendar_lock(business):
            mutation_lock_started.set()
            return real_lock_business_calendar(business)

        def mutation_worker():
            connections.close_all()
            web_client = Client()
            web_client.force_login(User.objects.get(pk=self.user.pk))
            try:
                if not availability_checked.wait(timeout=5):
                    raise AssertionError("La confirmación no revalidó el hueco.")
                with patch(
                    "apps.booking.views.lock_business_calendar",
                    side_effect=observed_calendar_lock,
                ):
                    response = web_client.post(mutation_url, mutation_data)
                return response.status_code, response.content.decode()
            finally:
                mutation_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            confirmation_future = executor.submit(confirm_worker)
            mutation_future = executor.submit(mutation_worker)
            confirmation_result = confirmation_future.result(timeout=10)
            mutation_status, mutation_content = mutation_future.result(timeout=10)

        return {
            "confirmation": confirmation_result,
            "mutation_status": mutation_status,
            "mutation_content": mutation_content,
            **state,
        }

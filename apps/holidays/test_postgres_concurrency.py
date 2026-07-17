from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from threading import Event, Lock
from unittest.mock import patch

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
    Service,
    WorkLine,
)
from apps.booking.services import (
    AppointmentDraft,
    cancel_appointment,
    confirm_appointment,
)
from apps.businesses.models import Business, BusinessMembership
from apps.customers.models import BusinessClient
from apps.holidays import appointment_reviews
from apps.holidays.appointment_reviews import acknowledge_holiday_appointment
from apps.holidays import services as holiday_services
from apps.holidays.models import (
    HolidayAppointmentReview,
    HolidaySyncRun,
    OfficialHoliday,
)
from apps.holidays.services import (
    BoeSyncError,
    BoeHolidayResolution,
    OfficialHolidayImport,
    sync_boe_national_holidays,
)


class FixedHolidayService:
    def __init__(self, resolution, holidays):
        self.resolution = resolution
        self.holidays = tuple(holidays)

    def fetch_national_holidays(self, target_year):
        return self.resolution, self.holidays


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLBoeCalendarConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(
            normalized_phone="+34600111777",
            phone="+34600111777",
            password="test-pass",
            full_name="Profesional BOE",
        )
        self.business = Business.objects.create(
            commercial_name="Salón BOE concurrente",
            slug="salon-boe-concurrente",
        )
        BusinessMembership.objects.create(business=self.business, user=self.user)
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=15,
            apply_national_holidays=True,
        )
        self.client_file = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente BOE",
        )
        self.work_line = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Línea 1",
        )
        self.service = Service.objects.create(
            business=self.business,
            name="Servicio BOE",
            duration_minutes=30,
        )
        self.target_date = timezone.localdate() + timedelta(days=14)
        AvailabilityRule.objects.create(
            business=self.business,
            weekday=self.target_date.weekday(),
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        self.starts_at = timezone.make_aware(
            datetime.combine(self.target_date, time(10, 0)),
            timezone.get_current_timezone(),
        )
        self.resolution = BoeHolidayResolution(
            identifier="BOE-A-CONCURRENCY",
            title="Calendario concurrente",
            url_html="https://www.boe.es/diario_boe/txt.php?id=BOE-A-CONCURRENCY",
        )
        self.holidays = (
            OfficialHolidayImport(self.target_date, "Festivo concurrente"),
        )

    def _holiday_service(self):
        return FixedHolidayService(self.resolution, self.holidays)

    def _create_confirmed_appointment(self):
        return Appointment.objects.create(
            business=self.business,
            business_client=self.client_file,
            work_line=self.work_line,
            starts_at=self.starts_at,
            ends_at=self.starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.user,
        )

    def test_confirmation_first_makes_sync_wait_and_snapshot_the_new_appointment(self):
        availability_checked = Event()
        sync_lock_started = Event()
        sync_finished = Event()
        state = {"sync_finished_before_confirmation": None}
        real_get_day_availability = booking_services.get_day_availability
        real_lock_all_calendars = holiday_services._lock_all_business_calendars

        def held_get_day_availability(*args, **kwargs):
            result = real_get_day_availability(*args, **kwargs)
            availability_checked.set()
            if not sync_lock_started.wait(timeout=5):
                raise AssertionError("La sincronización no intentó bloquear las agendas.")
            state["sync_finished_before_confirmation"] = sync_finished.wait(timeout=0.25)
            return result

        def observed_lock_all_calendars():
            sync_lock_started.set()
            return real_lock_all_calendars()

        def confirmation_worker():
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
                ), patch(
                    "apps.notifications.services.queue_appointment_emails",
                    return_value=(),
                ):
                    return confirm_appointment(draft).pk
            finally:
                connections.close_all()

        def sync_worker():
            connections.close_all()
            try:
                if not availability_checked.wait(timeout=5):
                    raise AssertionError("La confirmación no revalidó el hueco.")
                with patch(
                    "apps.holidays.services._lock_all_business_calendars",
                    side_effect=observed_lock_all_calendars,
                ):
                    return sync_boe_national_holidays(
                        self.target_date.year,
                        service=self._holiday_service(),
                    ).run.pk
            finally:
                sync_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            confirmation_future = executor.submit(confirmation_worker)
            sync_future = executor.submit(sync_worker)
            appointment_id = confirmation_future.result(timeout=10)
            run_id = sync_future.result(timeout=10)

        run = HolidaySyncRun.objects.get(pk=run_id)
        self.assertFalse(state["sync_finished_before_confirmation"])
        self.assertTrue(Appointment.objects.filter(pk=appointment_id).exists())
        self.assertEqual(run.affected_appointments, 1)
        self.assertEqual(run.affected_businesses, 1)

    def test_new_business_cannot_enter_while_global_calendar_snapshot_is_open(self):
        calendars_locked = Event()
        business_insert_started = Event()
        business_insert_committed = Event()
        appointment_attempt_finished = Event()
        state = {"business_committed_before_sync": None}
        real_lock_all_calendars = holiday_services._lock_all_business_calendars

        def held_lock_all_calendars():
            locked_calendars = real_lock_all_calendars()
            calendars_locked.set()
            if not business_insert_started.wait(timeout=5):
                raise AssertionError("El alta concurrente no intentó insertar el negocio.")
            state["business_committed_before_sync"] = business_insert_committed.wait(
                timeout=0.5
            )
            if state["business_committed_before_sync"] and not (
                appointment_attempt_finished.wait(timeout=5)
            ):
                raise AssertionError("La cita concurrente no terminó antes de continuar.")
            return locked_calendars

        def sync_worker():
            connections.close_all()
            try:
                with patch(
                    "apps.holidays.services._lock_all_business_calendars",
                    side_effect=held_lock_all_calendars,
                ):
                    return sync_boe_national_holidays(
                        self.target_date.year,
                        service=self._holiday_service(),
                    ).run.pk
            finally:
                connections.close_all()

        def new_business_worker():
            connections.close_all()
            try:
                if not calendars_locked.wait(timeout=5):
                    raise AssertionError("La sincronización no bloqueó las agendas.")
                business_insert_started.set()
                business = Business.objects.create(
                    commercial_name="Salón creado durante BOE",
                    slug="salon-creado-durante-boe",
                )
                business_insert_committed.set()
                professional = User.objects.create_user(
                    normalized_phone="+34600111888",
                    phone="+34600111888",
                    password="test-pass",
                    full_name="Profesional nuevo negocio",
                )
                BusinessMembership.objects.create(
                    business=business,
                    user=professional,
                )
                BusinessCalendarSettings.objects.create(
                    business=business,
                    slot_interval_minutes=15,
                    apply_national_holidays=True,
                )
                client_file = BusinessClient.objects.create(
                    business=business,
                    full_name="Cliente nuevo negocio",
                )
                work_line = WorkLine.objects.create(
                    business=business,
                    line_number=1,
                    name="Línea nuevo negocio",
                )
                service = Service.objects.create(
                    business=business,
                    name="Servicio nuevo negocio",
                    duration_minutes=30,
                )
                AvailabilityRule.objects.create(
                    business=business,
                    weekday=self.target_date.weekday(),
                    start_time=time(9, 0),
                    end_time=time(18, 0),
                )
                draft = AppointmentDraft(
                    business=business,
                    business_client=client_file,
                    services=(service,),
                    work_line_id=work_line.pk,
                    starts_at=self.starts_at,
                    duration_minutes=30,
                    channel=Appointment.ManualChannel.PHONE,
                    created_by=professional,
                )
                try:
                    with patch(
                        "apps.notifications.services.queue_appointment_emails",
                        return_value=(),
                    ):
                        confirm_appointment(draft)
                except ValidationError:
                    return "rejected"
                return "confirmed"
            finally:
                appointment_attempt_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            sync_future = executor.submit(sync_worker)
            business_future = executor.submit(new_business_worker)
            run_id = sync_future.result(timeout=10)
            appointment_result = business_future.result(timeout=10)

        run = HolidaySyncRun.objects.get(pk=run_id)
        self.assertFalse(state["business_committed_before_sync"])
        self.assertEqual(appointment_result, "rejected")
        self.assertEqual(run.affected_appointments, 0)
        self.assertEqual(run.affected_businesses, 0)
        self.assertFalse(
            Appointment.objects.filter(business__slug="salon-creado-durante-boe").exists()
        )

    def test_sync_first_makes_confirmation_wait_and_rejects_the_holiday_slot(self):
        snapshot_started = Event()
        confirmation_lock_started = Event()
        confirmation_finished = Event()
        state = {"confirmation_finished_before_sync": None}
        real_snapshot = holiday_services._locked_affected_future_appointments
        real_calendar_lock = booking_services.lock_business_calendar

        def held_snapshot(*args, **kwargs):
            snapshot_started.set()
            if not confirmation_lock_started.wait(timeout=5):
                raise AssertionError("La confirmación no intentó bloquear la agenda.")
            state["confirmation_finished_before_sync"] = confirmation_finished.wait(
                timeout=0.25
            )
            return real_snapshot(*args, **kwargs)

        def observed_calendar_lock(business):
            confirmation_lock_started.set()
            return real_calendar_lock(business)

        def sync_worker():
            connections.close_all()
            try:
                with patch(
                    "apps.holidays.services._locked_affected_future_appointments",
                    side_effect=held_snapshot,
                ):
                    return sync_boe_national_holidays(
                        self.target_date.year,
                        service=self._holiday_service(),
                    ).run.pk
            finally:
                connections.close_all()

        def confirmation_worker():
            connections.close_all()
            try:
                if not snapshot_started.wait(timeout=5):
                    raise AssertionError("La sincronización no alcanzó el resumen.")
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
                try:
                    with patch(
                        "apps.booking.services.lock_business_calendar",
                        side_effect=observed_calendar_lock,
                    ):
                        confirm_appointment(draft)
                except ValidationError:
                    return "rejected"
                return "committed"
            finally:
                confirmation_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            sync_future = executor.submit(sync_worker)
            confirmation_future = executor.submit(confirmation_worker)
            run_id = sync_future.result(timeout=10)
            confirmation_result = confirmation_future.result(timeout=10)

        run = HolidaySyncRun.objects.get(pk=run_id)
        self.assertFalse(state["confirmation_finished_before_sync"])
        self.assertEqual(confirmation_result, "rejected")
        self.assertEqual(run.affected_appointments, 0)
        self.assertEqual(run.affected_businesses, 0)
        self.assertFalse(Appointment.objects.exists())

    def test_sync_first_forces_a_concurrent_holiday_toggle_to_revalidate(self):
        self.business.calendar_settings.apply_national_holidays = False
        self.business.calendar_settings.save(update_fields=["apply_national_holidays"])
        self._create_confirmed_appointment()
        snapshot_started = Event()
        toggle_lock_started = Event()
        toggle_finished = Event()
        state = {"toggle_finished_before_sync": None}
        real_snapshot = holiday_services._locked_affected_future_appointments
        real_calendar_lock = booking_views.lock_business_calendar

        def held_snapshot(*args, **kwargs):
            snapshot_started.set()
            if not toggle_lock_started.wait(timeout=5):
                raise AssertionError("El cambio profesional no intentó bloquear la agenda.")
            state["toggle_finished_before_sync"] = toggle_finished.wait(timeout=0.25)
            return real_snapshot(*args, **kwargs)

        def observed_calendar_lock(business):
            toggle_lock_started.set()
            return real_calendar_lock(business)

        def sync_worker():
            connections.close_all()
            try:
                with patch(
                    "apps.holidays.services._locked_affected_future_appointments",
                    side_effect=held_snapshot,
                ):
                    return sync_boe_national_holidays(
                        self.target_date.year,
                        service=self._holiday_service(),
                    ).run.pk
            finally:
                connections.close_all()

        def toggle_worker():
            connections.close_all()
            web_client = Client()
            web_client.force_login(User.objects.get(pk=self.user.pk))
            try:
                if not snapshot_started.wait(timeout=5):
                    raise AssertionError("La sincronización no alcanzó el resumen.")
                with patch(
                    "apps.booking.views.lock_business_calendar",
                    side_effect=observed_calendar_lock,
                ):
                    response = web_client.post(
                        reverse("booking:professional_national_holidays_update"),
                        {"apply_national_holidays": "true"},
                    )
                return response.status_code
            finally:
                toggle_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            sync_future = executor.submit(sync_worker)
            toggle_future = executor.submit(toggle_worker)
            run_id = sync_future.result(timeout=10)
            toggle_status = toggle_future.result(timeout=10)

        run = HolidaySyncRun.objects.get(pk=run_id)
        self.business.calendar_settings.refresh_from_db()
        self.assertFalse(state["toggle_finished_before_sync"])
        self.assertEqual(toggle_status, 302)
        self.assertFalse(self.business.calendar_settings.apply_national_holidays)
        self.assertEqual(run.affected_appointments, 0)
        self.assertEqual(run.affected_businesses, 0)

    def test_impact_snapshot_locks_appointments_until_the_run_commits(self):
        appointment = self._create_confirmed_appointment()
        snapshot_locked = Event()
        cancellation_lock_started = Event()
        cancellation_finished = Event()
        state = {"cancellation_finished_before_sync": None}
        real_snapshot = holiday_services._locked_affected_future_appointments
        real_calendar_lock = booking_services.lock_business_calendar

        def held_snapshot(*args, **kwargs):
            result = real_snapshot(*args, **kwargs)
            snapshot_locked.set()
            if not cancellation_lock_started.wait(timeout=5):
                raise AssertionError("La cancelación no intentó bloquear la agenda.")
            state["cancellation_finished_before_sync"] = cancellation_finished.wait(
                timeout=0.25
            )
            return result

        def observed_calendar_lock(business):
            cancellation_lock_started.set()
            return real_calendar_lock(business)

        def sync_worker():
            connections.close_all()
            try:
                with patch(
                    "apps.holidays.services._locked_affected_future_appointments",
                    side_effect=held_snapshot,
                ):
                    return sync_boe_national_holidays(
                        self.target_date.year,
                        service=self._holiday_service(),
                    ).run.pk
            finally:
                connections.close_all()

        def cancellation_worker():
            connections.close_all()
            try:
                if not snapshot_locked.wait(timeout=5):
                    raise AssertionError("La sincronización no bloqueó el resumen.")
                with patch(
                    "apps.booking.services.lock_business_calendar",
                    side_effect=observed_calendar_lock,
                ), patch(
                    "apps.notifications.services.cancel_appointment_emails"
                ):
                    cancel_appointment(
                        Appointment.objects.get(pk=appointment.pk),
                        cancelled_by=User.objects.get(pk=self.user.pk),
                        reason="Cambio solicitado",
                    )
                return "cancelled"
            finally:
                cancellation_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            sync_future = executor.submit(sync_worker)
            cancellation_future = executor.submit(cancellation_worker)
            run_id = sync_future.result(timeout=10)
            cancellation_result = cancellation_future.result(timeout=10)

        run = HolidaySyncRun.objects.get(pk=run_id)
        appointment.refresh_from_db()
        self.assertFalse(state["cancellation_finished_before_sync"])
        self.assertEqual(cancellation_result, "cancelled")
        self.assertEqual(run.affected_appointments, 1)
        self.assertEqual(run.affected_businesses, 1)
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)

    def test_cancellation_first_serializes_before_sync_without_deadlock(self):
        appointment = self._create_confirmed_appointment()
        transition_locked = Event()
        sync_lock_started = Event()
        sync_finished = Event()
        state = {"sync_finished_before_transition": None}
        real_locked_appointment = booking_services._locked_appointment
        real_lock_all_calendars = holiday_services._lock_all_business_calendars

        def held_locked_appointment(current_appointment):
            locked_appointment = real_locked_appointment(current_appointment)
            transition_locked.set()
            if not sync_lock_started.wait(timeout=5):
                raise AssertionError("La sincronización no intentó bloquear las agendas.")
            state["sync_finished_before_transition"] = sync_finished.wait(timeout=0.25)
            return locked_appointment

        def observed_lock_all_calendars():
            sync_lock_started.set()
            return real_lock_all_calendars()

        def transition_worker():
            connections.close_all()
            try:
                current_appointment = Appointment.objects.get(pk=appointment.pk)
                actor = User.objects.get(pk=self.user.pk)
                with patch(
                    "apps.booking.services._locked_appointment",
                    side_effect=held_locked_appointment,
                ), patch("apps.notifications.services.cancel_appointment_emails"):
                    cancel_appointment(
                        current_appointment,
                        cancelled_by=actor,
                        reason="Cambio solicitado",
                    )
                return "cancelled"
            finally:
                connections.close_all()

        def sync_worker():
            connections.close_all()
            try:
                if not transition_locked.wait(timeout=5):
                    raise AssertionError("La transición no bloqueó la cita antes del BOE.")
                with patch(
                    "apps.holidays.services._lock_all_business_calendars",
                    side_effect=observed_lock_all_calendars,
                ):
                    return sync_boe_national_holidays(
                        self.target_date.year,
                        service=self._holiday_service(),
                    ).run.pk
            finally:
                sync_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            transition_future = executor.submit(transition_worker)
            sync_future = executor.submit(sync_worker)
            transition_result = transition_future.result(timeout=10)
            run_id = sync_future.result(timeout=10)

        run = HolidaySyncRun.objects.get(pk=run_id)
        appointment.refresh_from_db()
        self.assertFalse(state["sync_finished_before_transition"])
        self.assertEqual(transition_result, "cancelled")
        self.assertEqual(appointment.status, Appointment.Status.CANCELLED)
        self.assertEqual(run.affected_appointments, 0)
        self.assertEqual(run.affected_businesses, 0)

    def test_sync_first_makes_holiday_acknowledgement_wait_and_revalidate(self):
        appointment = self._create_confirmed_appointment()
        snapshot_started = Event()
        acknowledgement_lock_started = Event()
        acknowledgement_finished = Event()
        state = {"acknowledgement_finished_before_sync": None}
        real_snapshot = holiday_services._locked_affected_future_appointments
        real_calendar_lock = appointment_reviews.lock_business_calendar

        def held_snapshot(*args, **kwargs):
            snapshot_started.set()
            if not acknowledgement_lock_started.wait(timeout=5):
                raise AssertionError("El acuse no intentó bloquear la agenda.")
            state["acknowledgement_finished_before_sync"] = (
                acknowledgement_finished.wait(timeout=0.25)
            )
            return real_snapshot(*args, **kwargs)

        def observed_calendar_lock(business):
            acknowledgement_lock_started.set()
            return real_calendar_lock(business)

        def sync_worker():
            connections.close_all()
            try:
                with patch(
                    "apps.holidays.services._locked_affected_future_appointments",
                    side_effect=held_snapshot,
                ):
                    return sync_boe_national_holidays(
                        self.target_date.year,
                        service=self._holiday_service(),
                    ).run.pk
            finally:
                connections.close_all()

        def acknowledgement_worker():
            connections.close_all()
            try:
                if not snapshot_started.wait(timeout=5):
                    raise AssertionError("La sincronización no alcanzó el resumen.")
                with patch(
                    "apps.holidays.appointment_reviews.lock_business_calendar",
                    side_effect=observed_calendar_lock,
                ):
                    result = acknowledge_holiday_appointment(
                        business=Business.objects.get(pk=self.business.pk),
                        appointment_id=appointment.pk,
                        reviewed_by=User.objects.get(pk=self.user.pk),
                    )
                return result.review.pk
            finally:
                acknowledgement_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            sync_future = executor.submit(sync_worker)
            acknowledgement_future = executor.submit(acknowledgement_worker)
            run_id = sync_future.result(timeout=10)
            review_id = acknowledgement_future.result(timeout=10)

        run = HolidaySyncRun.objects.get(pk=run_id)
        review = HolidayAppointmentReview.objects.get(pk=review_id)
        self.assertFalse(state["acknowledgement_finished_before_sync"])
        self.assertEqual(run.affected_appointments, 1)
        self.assertEqual(run.affected_businesses, 1)
        self.assertEqual(review.appointment_id, appointment.pk)
        self.assertEqual(review.holiday_date, self.target_date)
        self.assertEqual(review.reviewed_by_id, self.user.pk)

    def test_acknowledgement_waiting_for_sync_rejects_if_the_start_time_passes(self):
        starts_at = timezone.now() + timedelta(seconds=3)
        holiday_day = timezone.localtime(starts_at).date()
        appointment = Appointment.objects.create(
            business=self.business,
            business_client=self.client_file,
            work_line=self.work_line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.user,
        )
        service = FixedHolidayService(
            BoeHolidayResolution(
                identifier="BOE-A-ACK-CROSSES-START",
                title="Calendario para cruce de inicio",
                url_html=(
                    "https://www.boe.es/diario_boe/"
                    "txt.php?id=BOE-A-ACK-CROSSES-START"
                ),
            ),
            (OfficialHolidayImport(holiday_day, "Festivo durante el cruce"),),
        )
        snapshot_locked = Event()
        acknowledgement_lock_started = Event()
        acknowledgement_finished = Event()
        state = {
            "acknowledgement_started_before_appointment": None,
            "acknowledgement_finished_before_sync": None,
        }
        real_snapshot = holiday_services._locked_affected_future_appointments
        real_calendar_lock = appointment_reviews.lock_business_calendar

        def held_snapshot(*args, **kwargs):
            result = real_snapshot(*args, **kwargs)
            snapshot_locked.set()
            if not acknowledgement_lock_started.wait(timeout=5):
                raise AssertionError("El acuse no intentó bloquear la agenda.")
            wait_until_after_start = max(
                0.0,
                (starts_at - timezone.now()).total_seconds(),
            ) + 0.3
            state["acknowledgement_finished_before_sync"] = (
                acknowledgement_finished.wait(timeout=wait_until_after_start)
            )
            return result

        def observed_calendar_lock(business):
            state["acknowledgement_started_before_appointment"] = (
                timezone.now() < starts_at
            )
            acknowledgement_lock_started.set()
            return real_calendar_lock(business)

        def sync_worker():
            connections.close_all()
            try:
                with patch(
                    "apps.holidays.services._locked_affected_future_appointments",
                    side_effect=held_snapshot,
                ):
                    return sync_boe_national_holidays(
                        holiday_day.year,
                        service=service,
                    ).run.pk
            finally:
                connections.close_all()

        def acknowledgement_worker():
            connections.close_all()
            try:
                if not snapshot_locked.wait(timeout=5):
                    raise AssertionError("La sincronización no bloqueó el resumen.")
                try:
                    with patch(
                        "apps.holidays.appointment_reviews.lock_business_calendar",
                        side_effect=observed_calendar_lock,
                    ):
                        acknowledge_holiday_appointment(
                            business=Business.objects.get(pk=self.business.pk),
                            appointment_id=appointment.pk,
                            reviewed_by=User.objects.get(pk=self.user.pk),
                        )
                except ValidationError as error:
                    return str(error)
                return "created"
            finally:
                acknowledgement_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            sync_future = executor.submit(sync_worker)
            acknowledgement_future = executor.submit(acknowledgement_worker)
            run_id = sync_future.result(timeout=15)
            acknowledgement_result = acknowledgement_future.result(timeout=15)

        run = HolidaySyncRun.objects.get(pk=run_id)
        self.assertTrue(state["acknowledgement_started_before_appointment"])
        self.assertFalse(state["acknowledgement_finished_before_sync"])
        self.assertIn("ya ha comenzado", acknowledgement_result)
        self.assertEqual(run.affected_appointments, 1)
        self.assertFalse(
            HolidayAppointmentReview.objects.filter(appointment=appointment).exists()
        )

    def test_different_years_fetch_concurrently_but_reconcile_one_at_a_time(self):
        first_year = self.target_date.year
        second_year = first_year + 1
        first_holiday = OfficialHolidayImport(
            date(first_year, 2, 1),
            "Festivo del primer año",
        )
        second_holiday = OfficialHolidayImport(
            date(second_year, 2, 1),
            "Festivo del segundo año",
        )
        first_fetch_started = Event()
        second_fetch_started = Event()

        class CoordinatedService:
            def __init__(
                inner_self,
                *,
                resolution,
                holiday,
                own_fetch_started,
                other_fetch_started,
            ):
                inner_self.resolution = resolution
                inner_self.holiday = holiday
                inner_self.own_fetch_started = own_fetch_started
                inner_self.other_fetch_started = other_fetch_started

            def fetch_national_holidays(inner_self, target_year):
                inner_self.own_fetch_started.set()
                if not inner_self.other_fetch_started.wait(timeout=5):
                    raise AssertionError(
                        "Las consultas externas de años distintos no coincidieron."
                    )
                return inner_self.resolution, (inner_self.holiday,)

        first_service = CoordinatedService(
            resolution=BoeHolidayResolution(
                identifier="BOE-A-GLOBAL-FIRST",
                title="Calendario del primer año",
                url_html="https://www.boe.es/diario_boe/txt.php?id=BOE-A-GLOBAL-FIRST",
            ),
            holiday=first_holiday,
            own_fetch_started=first_fetch_started,
            other_fetch_started=second_fetch_started,
        )
        second_service = CoordinatedService(
            resolution=BoeHolidayResolution(
                identifier="BOE-A-GLOBAL-SECOND",
                title="Calendario del segundo año",
                url_html="https://www.boe.es/diario_boe/txt.php?id=BOE-A-GLOBAL-SECOND",
            ),
            holiday=second_holiday,
            own_fetch_started=second_fetch_started,
            other_fetch_started=first_fetch_started,
        )

        order_guard = Lock()
        first_transaction_locked = Event()
        second_transaction_attempted = Event()
        second_transaction_locked = Event()
        state = {
            "lock_calls": 0,
            "second_locked_before_first_reconciled": None,
        }
        real_transaction_lock = holiday_services._lock_boe_reconciliation_transaction

        def observed_transaction_lock():
            with order_guard:
                state["lock_calls"] += 1
                position = state["lock_calls"]

            if position == 1:
                real_transaction_lock()
                first_transaction_locked.set()
                if not second_transaction_attempted.wait(timeout=5):
                    raise AssertionError(
                        "La segunda reconciliación no intentó tomar el bloqueo global."
                    )
                state["second_locked_before_first_reconciled"] = (
                    second_transaction_locked.wait(timeout=0.25)
                )
                return

            if not first_transaction_locked.wait(timeout=5):
                raise AssertionError(
                    "La primera reconciliación no tomó el bloqueo global."
                )
            second_transaction_attempted.set()
            real_transaction_lock()
            second_transaction_locked.set()

        def sync_worker(year, service):
            connections.close_all()
            try:
                return sync_boe_national_holidays(year, service=service).run.pk
            finally:
                connections.close_all()

        with patch(
            "apps.holidays.services._lock_boe_reconciliation_transaction",
            side_effect=observed_transaction_lock,
        ), ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(sync_worker, first_year, first_service)
            second_future = executor.submit(sync_worker, second_year, second_service)
            first_run_id = first_future.result(timeout=15)
            second_run_id = second_future.result(timeout=15)

        self.assertTrue(first_fetch_started.is_set())
        self.assertTrue(second_fetch_started.is_set())
        self.assertTrue(first_transaction_locked.is_set())
        self.assertTrue(second_transaction_locked.is_set())
        self.assertFalse(state["second_locked_before_first_reconciled"])
        self.assertEqual(state["lock_calls"], 2)
        self.assertEqual(
            set(
                HolidaySyncRun.objects.filter(
                    pk__in=(first_run_id, second_run_id)
                ).values_list("status", flat=True)
            ),
            {HolidaySyncRun.Status.SUCCESS},
        )
        self.assertTrue(
            OfficialHoliday.objects.filter(
                date=first_holiday.day,
                scope=OfficialHoliday.Scope.NATIONAL,
            ).exists()
        )
        self.assertTrue(
            OfficialHoliday.objects.filter(
                date=second_holiday.day,
                scope=OfficialHoliday.Scope.NATIONAL,
            ).exists()
        )


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLBoeAdvisoryLockTests(TransactionTestCase):
    def test_second_sync_is_rejected_before_fetch_while_the_year_is_locked(self):
        resolution = BoeHolidayResolution(
            identifier="BOE-A-FIRST-SYNC",
            title="Primer calendario",
            url_html="https://www.boe.es/diario_boe/txt.php?id=BOE-A-FIRST-SYNC",
        )
        holidays = (
            OfficialHolidayImport(
                timezone.localdate().replace(month=1, day=1),
                "Año Nuevo",
            ),
        )
        first_fetch_started = Event()
        release_first_fetch = Event()
        second_fetch_called = Event()

        class HeldFirstService:
            def fetch_national_holidays(inner_self, target_year):
                if connections["default"].in_atomic_block:
                    raise AssertionError("La descarga BOE se ejecutó dentro de atomic().")
                first_fetch_started.set()
                if not release_first_fetch.wait(timeout=5):
                    raise AssertionError("La primera descarga no fue liberada.")
                return resolution, holidays

        class UnexpectedSecondService:
            def fetch_national_holidays(inner_self, target_year):
                second_fetch_called.set()
                return resolution, holidays

        def first_worker():
            connections.close_all()
            try:
                return sync_boe_national_holidays(
                    holidays[0].day.year,
                    service=HeldFirstService(),
                ).run.pk
            finally:
                connections.close_all()

        def second_worker():
            connections.close_all()
            try:
                try:
                    sync_boe_national_holidays(
                        holidays[0].day.year,
                        service=UnexpectedSecondService(),
                    )
                except BoeSyncError as error:
                    return str(error)
                return "unexpected success"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(first_worker)
            self.assertTrue(first_fetch_started.wait(timeout=5))
            second_future = executor.submit(second_worker)
            conflict_message = second_future.result(timeout=5)
            release_first_fetch.set()
            first_run_id = first_future.result(timeout=10)

        self.assertIn("Ya hay una sincronización del BOE en curso", conflict_message)
        self.assertFalse(second_fetch_called.is_set())
        self.assertEqual(HolidaySyncRun.objects.count(), 1)
        self.assertEqual(
            HolidaySyncRun.objects.get(pk=first_run_id).status,
            HolidaySyncRun.Status.SUCCESS,
        )
        self.assertEqual(
            OfficialHoliday.objects.filter(
                date=holidays[0].day,
                scope=OfficialHoliday.Scope.NATIONAL,
            ).count(),
            1,
        )

    def test_year_lock_is_released_after_a_failed_fetch(self):
        resolution = BoeHolidayResolution(
            identifier="BOE-A-RETRY",
            title="Calendario tras reintento",
            url_html="https://www.boe.es/diario_boe/txt.php?id=BOE-A-RETRY",
        )
        target_year = timezone.localdate().year
        holidays = (
            OfficialHolidayImport(date(target_year, 1, 1), "Año Nuevo"),
        )
        failure_finished = Event()
        retry_finished = Event()

        class FailingService:
            def fetch_national_holidays(inner_self, requested_year):
                raise BoeSyncError("Fallo BOE controlado")

        def failing_worker():
            connections.close_all()
            try:
                try:
                    sync_boe_national_holidays(target_year, service=FailingService())
                except BoeSyncError as error:
                    failure_finished.set()
                    if not retry_finished.wait(timeout=5):
                        raise AssertionError("El reintento no terminó.") from error
                    return str(error)
                return "unexpected success"
            finally:
                connections.close_all()

        def retry_worker():
            connections.close_all()
            try:
                if not failure_finished.wait(timeout=5):
                    raise AssertionError("La sincronización fallida no terminó.")
                return sync_boe_national_holidays(
                    target_year,
                    service=FixedHolidayService(resolution, holidays),
                ).run.pk
            finally:
                retry_finished.set()
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            failure_future = executor.submit(failing_worker)
            retry_future = executor.submit(retry_worker)
            retry_run_id = retry_future.result(timeout=10)
            failure_message = failure_future.result(timeout=10)

        self.assertEqual(failure_message, "Fallo BOE controlado")
        self.assertEqual(HolidaySyncRun.objects.count(), 2)
        self.assertEqual(
            HolidaySyncRun.objects.get(pk=retry_run_id).status,
            HolidaySyncRun.Status.SUCCESS,
        )
        self.assertEqual(
            HolidaySyncRun.objects.filter(status=HolidaySyncRun.Status.FAILED).count(),
            1,
        )

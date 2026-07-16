from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import User
from apps.booking.forms import AppointmentSearchForm
from apps.booking.models import (
    Appointment,
    AppointmentService,
    BusinessCalendarSettings,
    Service,
    WorkLine,
)
from apps.booking.services import (
    AppointmentDraft,
    complete_appointment,
    confirm_appointment,
    mark_appointment_no_show,
)
from apps.businesses.models import Business
from apps.customers.models import BusinessClient


MADRID = ZoneInfo("Europe/Madrid")


class BookingModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            normalized_phone="+34600111002",
            password="test-pass",
            full_name="Mari Profesional",
        )
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )
        self.client = BusinessClient.objects.create(
            business=self.business,
            full_name="Lucía Gómez",
            phone="600111333",
        )
        self.work_line = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Linea 1",
        )

    def appointment(self, start, minutes=60, status=Appointment.Status.CONFIRMED):
        appointment = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.work_line,
            starts_at=start,
            ends_at=start + timedelta(minutes=minutes),
            total_duration_minutes=minutes,
            status=status,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.user,
        )
        appointment.full_clean()
        appointment.save()
        return appointment

    def confirmation_draft(self, service, *, duration_minutes, reason=""):
        return AppointmentDraft(
            business=self.business,
            business_client=self.client,
            services=(service,),
            work_line_id=self.work_line.pk,
            starts_at=datetime.now(tz=MADRID) + timedelta(days=2),
            duration_minutes=duration_minutes,
            duration_adjustment_reason=reason,
            channel=Appointment.ManualChannel.PHONE,
            created_by=self.user,
        )

    def test_service_duration_must_use_15_minute_slots(self):
        service = Service(
            business=self.business,
            name="Servicio raro",
            duration_minutes=20,
        )

        with self.assertRaises(ValidationError):
            service.full_clean()

    def test_service_duration_must_match_the_business_calendar_interval(self):
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=30,
        )
        service = Service(
            business=self.business,
            name="Servicio incompatible",
            duration_minutes=45,
        )

        with self.assertRaises(ValidationError) as context:
            service.full_clean()

        self.assertIn("intervalo de agenda de 30 minutos", str(context.exception))

    def test_confirmation_rejects_legacy_incompatible_total_duration(self):
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=30,
        )
        service = Service.objects.create(
            business=self.business,
            name="Servicio de 30 minutos",
            duration_minutes=30,
        )

        with self.assertRaises(ValidationError) as context:
            confirm_appointment(
                self.confirmation_draft(
                    service,
                    duration_minutes=45,
                    reason="Ajuste heredado",
                )
            )

        self.assertIn("intervalo de agenda de 30 minutos", str(context.exception))

    def test_confirmation_rejects_active_legacy_incompatible_service(self):
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=30,
        )
        service = Service.objects.create(
            business=self.business,
            name="Servicio heredado de 45 minutos",
            duration_minutes=45,
        )

        with self.assertRaises(ValidationError) as context:
            confirm_appointment(
                self.confirmation_draft(
                    service,
                    duration_minutes=60,
                    reason="Ajuste heredado",
                )
            )

        self.assertIn("ya no es compatible", str(context.exception))

    def test_confirmation_requires_reason_when_total_differs_from_services(self):
        service = Service.objects.create(
            business=self.business,
            name="Servicio de 30 minutos",
            duration_minutes=30,
        )

        with self.assertRaises(ValidationError) as context:
            confirm_appointment(
                self.confirmation_draft(service, duration_minutes=45)
            )

        self.assertIn("motivo del ajuste", str(context.exception))

    def test_calendar_interval_cannot_invalidate_an_active_service(self):
        Service.objects.create(
            business=self.business,
            name="Servicio de 45 minutos",
            duration_minutes=45,
        )
        calendar = BusinessCalendarSettings(
            business=self.business,
            slot_interval_minutes=30,
        )

        with self.assertRaises(ValidationError) as context:
            calendar.full_clean()

        self.assertIn("Servicio de 45 minutos", str(context.exception))

    def test_historical_appointment_keeps_its_duration_after_interval_changes(self):
        appointment = self.appointment(
            datetime(2026, 7, 1, 9, 0, tzinfo=MADRID),
            minutes=45,
        )
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=30,
        )

        appointment.full_clean()

    def test_appointment_search_rejects_an_incompatible_duration_adjustment(self):
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=30,
        )
        service = Service.objects.create(
            business=self.business,
            name="Servicio de 30 minutos",
            duration_minutes=30,
        )
        form = AppointmentSearchForm(
            data={
                "business_client": self.client.pk,
                "manual_channel": Appointment.ManualChannel.PHONE,
                "requested_by_contact": "self",
                "services": [service.pk],
                "target_date": "2026-08-01",
                "adjusted_duration_minutes": 45,
                "duration_adjustment_reason": "Necesita más tiempo.",
            },
            business=self.business,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("intervalo de agenda de 30 minutos", form.errors["adjusted_duration_minutes"][0])

    def test_appointment_search_rejects_an_incompatible_service_sum(self):
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=30,
        )
        incompatible_service = Service.objects.create(
            business=self.business,
            name="Servicio heredado de 45 minutos",
            duration_minutes=45,
        )
        form = AppointmentSearchForm(
            data={
                "business_client": self.client.pk,
                "manual_channel": Appointment.ManualChannel.PHONE,
                "requested_by_contact": "self",
                "services": [incompatible_service.pk],
                "target_date": "2026-08-01",
            },
            business=self.business,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("intervalo de agenda de 30 minutos", form.errors["services"][0])

    def test_work_line_number_is_limited_to_three(self):
        line = WorkLine(
            business=self.business,
            line_number=4,
            name="Linea 4",
        )

        with self.assertRaises(ValidationError):
            line.full_clean()

    def test_confirmed_appointments_cannot_overlap_in_same_line(self):
        self.appointment(datetime(2026, 7, 1, 9, 0, tzinfo=MADRID), minutes=60)

        overlapping = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.work_line,
            starts_at=datetime(2026, 7, 1, 9, 30, tzinfo=MADRID),
            ends_at=datetime(2026, 7, 1, 10, 30, tzinfo=MADRID),
            total_duration_minutes=60,
            status=Appointment.Status.CONFIRMED,
            created_by=self.user,
        )

        with self.assertRaises(ValidationError):
            overlapping.full_clean()

    def test_cancelled_appointments_do_not_block_the_line(self):
        self.appointment(
            datetime(2026, 7, 1, 9, 0, tzinfo=MADRID),
            minutes=60,
            status=Appointment.Status.CANCELLED,
        )

        confirmed = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.work_line,
            starts_at=datetime(2026, 7, 1, 9, 30, tzinfo=MADRID),
            ends_at=datetime(2026, 7, 1, 10, 30, tzinfo=MADRID),
            total_duration_minutes=60,
            status=Appointment.Status.CONFIRMED,
            created_by=self.user,
        )

        confirmed.full_clean()

    def test_future_appointment_cannot_be_completed(self):
        start = datetime.now(tz=MADRID) + timedelta(days=2)
        ends_at = start + timedelta(minutes=60)
        appointment = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.work_line,
            starts_at=start,
            ends_at=ends_at,
            total_duration_minutes=60,
            status=Appointment.Status.COMPLETED,
            completed_at=ends_at,
            created_by=self.user,
        )

        with self.assertRaises(ValidationError) as context:
            appointment.full_clean()

        self.assertIn("cita futura", str(context.exception))

    def test_future_appointment_cannot_be_marked_no_show_with_future_timestamp(self):
        start = datetime.now(tz=MADRID) + timedelta(days=2)
        ends_at = start + timedelta(minutes=60)
        appointment = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.work_line,
            starts_at=start,
            ends_at=ends_at,
            total_duration_minutes=60,
            status=Appointment.Status.NO_SHOW,
            no_show_marked_at=ends_at,
            created_by=self.user,
        )

        with self.assertRaises(ValidationError) as context:
            appointment.full_clean()

        self.assertIn("cita futura", str(context.exception))

    def test_completed_at_cannot_precede_the_appointment_end(self):
        starts_at = datetime(2026, 7, 1, 9, 0, tzinfo=MADRID)
        appointment = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.work_line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=60),
            total_duration_minutes=60,
            status=Appointment.Status.COMPLETED,
            completed_at=starts_at + timedelta(minutes=59),
            created_by=self.user,
        )

        with self.assertRaises(ValidationError) as context:
            appointment.full_clean()

        self.assertIn("antes de terminar", str(context.exception))

    def test_no_show_marked_at_cannot_precede_the_appointment_end(self):
        starts_at = datetime(2026, 7, 1, 9, 0, tzinfo=MADRID)
        appointment = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.work_line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=60),
            total_duration_minutes=60,
            status=Appointment.Status.NO_SHOW,
            no_show_marked_at=starts_at + timedelta(minutes=59),
            created_by=self.user,
        )

        with self.assertRaises(ValidationError) as context:
            appointment.full_clean()

        self.assertIn("antes de que termine", str(context.exception))

    def test_database_rejects_completed_status_without_valid_timestamp(self):
        appointment = self.appointment(
            datetime.now(tz=MADRID) - timedelta(hours=2),
            minutes=60,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.filter(pk=appointment.pk).update(
                status=Appointment.Status.COMPLETED,
                completed_at=None,
            )

    def test_database_rejects_no_show_timestamp_before_end(self):
        appointment = self.appointment(
            datetime.now(tz=MADRID) - timedelta(hours=2),
            minutes=60,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Appointment.objects.filter(pk=appointment.pk).update(
                status=Appointment.Status.NO_SHOW,
                no_show_marked_at=appointment.ends_at - timedelta(minutes=1),
            )

    def test_outcomes_are_allowed_exactly_at_the_appointment_end(self):
        first_start = datetime(2026, 7, 1, 9, 0, tzinfo=MADRID)
        completed = self.appointment(first_start, minutes=60)
        no_show = self.appointment(first_start + timedelta(hours=1), minutes=60)

        complete_appointment(
            completed,
            completed_by=self.user,
            at=completed.ends_at,
        )
        mark_appointment_no_show(
            no_show,
            marked_by=self.user,
            at=no_show.ends_at,
        )

        completed.refresh_from_db()
        no_show.refresh_from_db()
        self.assertEqual(completed.status, Appointment.Status.COMPLETED)
        self.assertEqual(completed.completed_at, completed.ends_at)
        self.assertEqual(no_show.status, Appointment.Status.NO_SHOW)
        self.assertEqual(no_show.no_show_marked_at, no_show.ends_at)

    def test_stale_appointment_cannot_overwrite_an_already_recorded_outcome(self):
        appointment = self.appointment(
            datetime(2026, 7, 1, 9, 0, tzinfo=MADRID),
            minutes=60,
        )
        first_copy = Appointment.objects.get(pk=appointment.pk)
        stale_copy = Appointment.objects.get(pk=appointment.pk)

        complete_appointment(
            first_copy,
            completed_by=self.user,
            at=datetime(2026, 7, 1, 10, 30, tzinfo=MADRID),
        )

        with self.assertRaises(ValidationError):
            mark_appointment_no_show(
                stale_copy,
                marked_by=self.user,
                at=datetime(2026, 7, 1, 10, 31, tzinfo=MADRID),
            )

        appointment.refresh_from_db()
        self.assertEqual(appointment.status, Appointment.Status.COMPLETED)

    def test_appointment_service_copies_service_snapshot(self):
        service = Service.objects.create(
            business=self.business,
            name="Corte",
            duration_minutes=30,
            price_amount="18.00",
            color_hex="#C56B5C",
        )
        appointment = self.appointment(
            datetime(2026, 7, 1, 11, 0, tzinfo=MADRID),
            minutes=30,
        )

        item = AppointmentService.objects.create(
            appointment=appointment,
            service=service,
            display_order=1,
        )

        self.assertEqual(item.service_name_snapshot, "Corte")
        self.assertEqual(item.duration_minutes_snapshot, 30)
        self.assertEqual(str(item.price_amount_snapshot), "18.00")
        self.assertEqual(item.color_hex_snapshot, "#C56B5C")

    def test_adjusted_duration_requires_reason_once_services_exist(self):
        service = Service.objects.create(
            business=self.business,
            name="Corte",
            duration_minutes=30,
        )
        appointment = self.appointment(
            datetime(2026, 7, 1, 12, 0, tzinfo=MADRID),
            minutes=45,
        )
        AppointmentService.objects.create(
            appointment=appointment,
            service=service,
            display_order=1,
        )

        with self.assertRaises(ValidationError):
            appointment.full_clean()

        appointment.duration_adjustment_reason = "Cliente requiere margen extra."
        appointment.full_clean()

# Create your tests here.

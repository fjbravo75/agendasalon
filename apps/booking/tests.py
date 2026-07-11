from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import User
from apps.booking.models import Appointment, AppointmentService, Service, WorkLine
from apps.booking.services import complete_appointment, mark_appointment_no_show
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

    def test_service_duration_must_use_15_minute_slots(self):
        service = Service(
            business=self.business,
            name="Servicio raro",
            duration_minutes=20,
        )

        with self.assertRaises(ValidationError):
            service.full_clean()

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
        appointment = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.work_line,
            starts_at=start,
            ends_at=start + timedelta(minutes=60),
            total_duration_minutes=60,
            status=Appointment.Status.COMPLETED,
            created_by=self.user,
        )

        with self.assertRaises(ValidationError):
            appointment.full_clean()

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

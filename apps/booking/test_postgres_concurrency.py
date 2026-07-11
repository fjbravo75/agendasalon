from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.core.exceptions import ValidationError
from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.accounts.models import User
from apps.booking.models import Appointment, WorkLine
from apps.booking.services import complete_appointment, mark_appointment_no_show
from apps.businesses.models import Business, BusinessActivityEvent
from apps.customers.models import BusinessClient


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

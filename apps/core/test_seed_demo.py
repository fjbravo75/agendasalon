from datetime import date, datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.booking.models import (
    Appointment,
    AppointmentService,
    AvailabilityRule,
    BusinessCalendarSettings,
    BusinessClosure,
    Service,
    WorkLine,
)
from apps.booking.slot_engine import get_day_availability, suggest_next_slots
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.customers.models import BusinessClient, BusinessClientAccess, BusinessClientAuthorizedContact
from apps.holidays.models import HolidaySyncRun, OfficialHoliday
from apps.notifications.models import InternalNotification


MADRID = ZoneInfo("Europe/Madrid")


class SeedDemoCommandTests(TestCase):
    def test_seed_demo_creates_required_demo_data_and_is_idempotent(self):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        first_counts = self._counts()

        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        second_counts = self._counts()

        self.assertEqual(first_counts, second_counts)

        business = Business.objects.get(slug="peluqueria-mari")
        barberia = Business.objects.get(slug="barberia-norte")
        self.assertTrue(business.is_operational_for_agenda())
        self.assertTrue(barberia.is_operational_for_agenda())
        self.assertTrue(business.public_booking_enabled)
        self.assertTrue(barberia.public_booking_enabled)
        self.assertEqual(Business.objects.filter(slug="barberia-norte-demo").count(), 0)
        self.assertEqual(Business.objects.filter(is_active=True).count(), 2)
        self.assertTrue(
            BusinessMembership.objects.filter(
                business=barberia,
                user__normalized_phone="+34600222001",
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            all(
                identify_hasher(user.password).algorithm == "argon2"
                for user in get_user_model().objects.all()
            )
        )
        self.assertEqual(BusinessCalendarSettings.objects.filter(business=business).count(), 1)
        self.assertEqual(BusinessCalendarSettings.objects.filter(business=barberia).count(), 1)
        self.assertEqual(WorkLine.objects.filter(business=business, is_active=True).count(), 3)
        self.assertEqual(WorkLine.objects.filter(business=barberia, is_active=True).count(), 2)
        self.assertEqual(Service.objects.filter(business=business).count(), 7)
        self.assertEqual(Service.objects.filter(business=business, is_active=True).count(), 6)
        self.assertEqual(Service.objects.filter(business=barberia, is_active=True).count(), 5)
        self.assertEqual(AvailabilityRule.objects.filter(business=business, is_active=True).count(), 11)
        self.assertEqual(AvailabilityRule.objects.filter(business=barberia, is_active=True).count(), 11)
        self.assertEqual(BusinessClient.objects.filter(business=business).count(), 5)
        self.assertEqual(BusinessClient.objects.filter(business=barberia).count(), 2)
        self.assertEqual(BusinessClientAccess.objects.filter(business=business).count(), 2)
        self.assertEqual(BusinessClientAccess.objects.filter(business=barberia).count(), 1)
        self.assertTrue(
            all(
                identify_hasher(access.password_hash).algorithm == "argon2"
                for access in BusinessClientAccess.objects.all()
            )
        )
        self.assertEqual(BusinessClientAuthorizedContact.objects.filter(business=business).count(), 2)
        self.assertEqual(BusinessClosure.objects.filter(business=business, is_active=True).count(), 2)
        self.assertEqual(OfficialHoliday.objects.filter(name="Fiesta nacional").count(), 1)
        self.assertEqual(HolidaySyncRun.objects.filter(source_name="Calendario local AgendaSalon").count(), 1)
        self.assertEqual(InternalNotification.objects.filter(business=business).count(), 4)
        self.assertEqual(BusinessActivityEvent.objects.filter(business=business).count(), 3)
        self.assertEqual(BusinessActivityEvent.objects.filter(business=barberia).count(), 2)
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=business,
                origin=BusinessActivityEvent.Origin.PUBLIC_WEB,
            ).exists()
        )
        activity_events = BusinessActivityEvent.objects.all()
        self.assertTrue(all(event.created_at <= timezone.now() for event in activity_events))
        self.assertTrue(activity_events.filter(summary__contains="06/07/2026").exists())
        self.assertTrue(activity_events.filter(summary__contains="09/07/2026").exists())

        self.assertTrue(Appointment.objects.filter(business=business, status=Appointment.Status.CONFIRMED).exists())
        self.assertTrue(Appointment.objects.filter(business=business, status=Appointment.Status.CANCELLED).exists())
        self.assertTrue(Appointment.objects.filter(business=business, status=Appointment.Status.COMPLETED).exists())
        self.assertTrue(Appointment.objects.filter(business=business, status=Appointment.Status.NO_SHOW).exists())
        self.assertEqual(Appointment.objects.filter(business=barberia).count(), 3)
        self.assertTrue(
            Appointment.objects.filter(
                business=barberia,
                status=Appointment.Status.CONFIRMED,
            ).exists()
        )
        self.assertTrue(
            Appointment.objects.filter(
                business=barberia,
                status=Appointment.Status.COMPLETED,
            ).exists()
        )
        self.assertTrue(
            Appointment.objects.filter(
                business=barberia,
                status=Appointment.Status.CANCELLED,
            ).exists()
        )

        combined = Appointment.objects.get(
            business=business,
            business_client__full_name="Lucía Gómez",
            starts_at=datetime(2026, 7, 6, 16, 0, tzinfo=MADRID),
        )
        self.assertEqual(combined.total_duration_minutes, 180)
        self.assertEqual(combined.appointment_services.count(), 4)

        no_capacity = get_day_availability(
            business=business,
            target_date=date(2026, 7, 8),
            duration_minutes=180,
            now=datetime(2026, 7, 1, 8, 0, tzinfo=MADRID),
        )
        self.assertFalse(no_capacity.has_slots)

        suggestions = suggest_next_slots(
            business=business,
            start_date=date(2026, 7, 8),
            duration_minutes=180,
            now=datetime(2026, 7, 1, 8, 0, tzinfo=MADRID),
            limit=1,
        )
        self.assertEqual(suggestions[0].starts_at.date(), date(2026, 7, 9))

    def test_seed_demo_restores_internal_demo_credentials_and_removes_password_gate(self):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        User = get_user_model()
        demo_phones = (
            "+34910000001",
            "+34600111001",
            "+34600222001",
        )

        for user in User.objects.filter(normalized_phone__in=demo_phones):
            user.set_password("Contraseña modificada durante la prueba 2026")
            user.password_change_required = True
            user.save(update_fields=["password", "password_change_required"])

        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())

        restored_users = User.objects.filter(normalized_phone__in=demo_phones)
        self.assertEqual(restored_users.count(), 3)
        for user in restored_users:
            self.assertTrue(user.check_password("DemoAgendaSalon2026!"))
            self.assertFalse(user.check_password("Contraseña modificada durante la prueba 2026"))
            self.assertFalse(user.password_change_required)

    def test_seed_demo_never_dates_calendar_trace_in_the_future(self):
        future_base_date = timezone.localdate() + timedelta(days=30)

        call_command("seed_demo", base_date=future_base_date.isoformat(), stdout=StringIO())

        run = HolidaySyncRun.objects.get(source_name="Calendario local AgendaSalon")
        self.assertLessEqual(run.started_at, run.finished_at)
        self.assertLessEqual(run.finished_at, timezone.now())

    def test_seed_demo_merges_service_names_that_only_differ_by_accents(self):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        business = Business.objects.get(slug="peluqueria-mari")
        Service.objects.create(
            business=business,
            name="Moldeador clasico",
            description="Nombre antiguo",
            duration_minutes=60,
            price_amount="40.00",
            is_active=False,
        )

        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())

        matching_names = [
            service.name
            for service in Service.objects.filter(business=business)
            if service.name.lower().replace("á", "a") == "moldeador clasico"
        ]
        self.assertEqual(matching_names, ["Moldeador clásico"])
        self.assertEqual(Service.objects.filter(business=business).count(), 7)

    def test_seed_demo_resets_operational_records_when_the_demo_week_changes(self):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        old_appointment_ids = set(Appointment.objects.values_list("id", flat=True))

        call_command("seed_demo", base_date="2026-07-13", stdout=StringIO())

        self.assertFalse(
            Appointment.objects.filter(pk__in=old_appointment_ids).exists()
        )
        self.assertTrue(
            Appointment.objects.filter(
                business__slug="peluqueria-mari",
                starts_at__date=date(2026, 7, 13),
            ).exists()
        )
        self.assertEqual(BusinessActivityEvent.objects.count(), 5)
        self.assertFalse(
            BusinessActivityEvent.objects.filter(summary__contains="06/07/2026").exists()
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(summary__contains="13/07/2026").exists()
        )

    def _counts(self):
        User = get_user_model()
        return {
            "users": User.objects.count(),
            "businesses": Business.objects.count(),
            "memberships": BusinessMembership.objects.count(),
            "calendar_settings": BusinessCalendarSettings.objects.count(),
            "availability_rules": AvailabilityRule.objects.count(),
            "services": Service.objects.count(),
            "work_lines": WorkLine.objects.count(),
            "clients": BusinessClient.objects.count(),
            "client_accesses": BusinessClientAccess.objects.count(),
            "contacts": BusinessClientAuthorizedContact.objects.count(),
            "closures": BusinessClosure.objects.count(),
            "holidays": OfficialHoliday.objects.count(),
            "holiday_runs": HolidaySyncRun.objects.count(),
            "appointments": Appointment.objects.count(),
            "appointment_services": AppointmentService.objects.count(),
            "notifications": InternalNotification.objects.count(),
            "activity_events": BusinessActivityEvent.objects.count(),
        }

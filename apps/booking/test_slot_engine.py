from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase

from apps.accounts.models import User
from apps.booking.models import (
    Appointment,
    AvailabilityRule,
    BusinessCalendarSettings,
    BusinessClosure,
    WorkLine,
)
from apps.booking.slot_engine import (
    STATUS_AVAILABLE,
    STATUS_CLOSED,
    get_day_availability,
    get_month_availability,
    suggest_next_slots,
)
from apps.businesses.models import Business
from apps.customers.models import BusinessClient
from apps.holidays.models import OfficialHoliday


MADRID = ZoneInfo("Europe/Madrid")


def aware_datetime(year, month, day, hour, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=MADRID)


class SlotEngineTests(TestCase):
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
        BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=15,
            apply_national_holidays=True,
        )
        self.client = BusinessClient.objects.create(
            business=self.business,
            full_name="Lucía Gómez",
            phone="600111333",
        )
        self.line_1 = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Linea 1",
            display_order=1,
        )
        self.line_2 = WorkLine.objects.create(
            business=self.business,
            line_number=2,
            name="Linea 2",
            display_order=2,
        )
        self.future_now = aware_datetime(2026, 7, 1, 8, 0)

    def add_availability(self, target_date, start_at, end_at):
        return AvailabilityRule.objects.create(
            business=self.business,
            weekday=target_date.weekday(),
            start_time=start_at,
            end_time=end_at,
        )

    def add_appointment(self, starts_at, minutes=60, status=Appointment.Status.CONFIRMED):
        appointment = Appointment(
            business=self.business,
            business_client=self.client,
            work_line=self.line_1,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=minutes),
            total_duration_minutes=minutes,
            status=status,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.user,
        )
        appointment.full_clean()
        appointment.save()
        return appointment

    def test_day_availability_respects_weekly_schedule(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(12, 0))

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=self.future_now,
        )

        line_slots = result.slots_by_line[self.line_1.id]
        self.assertEqual(result.status, STATUS_AVAILABLE)
        self.assertEqual(line_slots[0].starts_at.time(), time(9, 0))
        self.assertEqual(line_slots[-1].starts_at.time(), time(11, 0))
        self.assertTrue(all(slot.starts_at.time() >= time(9, 0) for slot in line_slots))
        self.assertTrue(all(slot.ends_at.time() <= time(12, 0) for slot in line_slots))

    def test_day_availability_uses_only_active_work_lines(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(12, 0))
        self.line_2.is_active = False
        self.line_2.save()

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=self.future_now,
        )

        self.assertEqual(tuple(result.slots_by_line.keys()), (self.line_1.id,))

    def test_confirmed_appointments_remove_overlapping_candidates(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(12, 0))
        self.line_2.is_active = False
        self.line_2.save()
        self.add_appointment(aware_datetime(2026, 7, 6, 10, 0), minutes=60)

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=self.future_now,
        )

        self.assertEqual(
            [slot.starts_at.time() for slot in result.slots_by_line[self.line_1.id]],
            [time(9, 0), time(11, 0)],
        )

    def test_cancelled_appointments_do_not_block_candidates(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(12, 0))
        self.line_2.is_active = False
        self.line_2.save()
        self.add_appointment(
            aware_datetime(2026, 7, 6, 10, 0),
            minutes=60,
            status=Appointment.Status.CANCELLED,
        )

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=self.future_now,
        )

        self.assertIn(
            time(10, 0),
            [slot.starts_at.time() for slot in result.slots_by_line[self.line_1.id]],
        )

    def test_punctual_block_removes_overlapping_candidates(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(12, 0))
        self.line_2.is_active = False
        self.line_2.save()
        BusinessClosure.objects.create(
            business=self.business,
            work_line=self.line_1,
            date_from=target_date,
            date_to=target_date,
            start_time=time(10, 0),
            end_time=time(11, 0),
            closure_type=BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
            internal_reason="Bloqueo de mostrador",
            created_by=self.user,
        )

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=self.future_now,
        )

        self.assertEqual(
            [slot.starts_at.time() for slot in result.slots_by_line[self.line_1.id]],
            [time(9, 0), time(11, 0)],
        )

    def test_full_business_closure_returns_closed_day(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(12, 0))
        BusinessClosure.objects.create(
            business=self.business,
            date_from=target_date,
            date_to=target_date,
            closure_type=BusinessClosure.ClosureType.BUSINESS_CLOSURE,
            internal_reason="Cierre por reforma",
            created_by=self.user,
        )

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=self.future_now,
        )

        self.assertEqual(result.status, STATUS_CLOSED)
        self.assertEqual(result.reason, "cierre_negocio")
        self.assertFalse(result.has_slots)

    def test_national_holiday_closes_business_when_setting_applies(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(12, 0))
        OfficialHoliday.objects.create(
            date=target_date,
            name="Festivo nacional demo",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="Demo",
        )

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=self.future_now,
        )

        self.assertEqual(result.status, STATUS_CLOSED)
        self.assertEqual(result.reason, "festivo_nacional")
        self.assertFalse(result.has_slots)

    def test_duration_total_discards_free_ranges_that_are_too_short(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(11, 0))

        result_120 = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=120,
            now=self.future_now,
        )
        result_180 = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=180,
            now=self.future_now,
        )

        self.assertEqual(result_120.slots_by_line[self.line_1.id][0].starts_at.time(), time(9, 0))
        self.assertFalse(result_180.has_slots)
        self.assertEqual(result_180.reason, "sin_hueco")

    def test_today_search_starts_from_now_rounded_to_next_slot(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(12, 0))
        self.line_2.is_active = False
        self.line_2.save()

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=aware_datetime(2026, 7, 6, 10, 7),
        )

        self.assertEqual(result.calculated_from.time(), time(10, 15))
        self.assertEqual(
            [slot.starts_at.time() for slot in result.slots_by_line[self.line_1.id]],
            [time(10, 15), time(10, 30), time(10, 45), time(11, 0)],
        )

    def test_month_availability_marks_only_days_with_real_capacity(self):
        available_day = date(2026, 7, 6)
        insufficient_day = date(2026, 7, 7)
        self.add_availability(available_day, time(9, 0), time(12, 0))
        self.add_availability(insufficient_day, time(9, 0), time(11, 0))

        result = get_month_availability(
            business=self.business,
            year=2026,
            month=7,
            duration_minutes=180,
            now=self.future_now,
        )

        self.assertIn(available_day, result.available_dates)
        self.assertNotIn(insufficient_day, result.available_dates)

    def test_suggestions_return_next_real_slots_when_selected_day_has_no_capacity(self):
        selected_day = date(2026, 7, 7)
        next_available_day = date(2026, 7, 8)
        self.add_availability(selected_day, time(9, 0), time(10, 0))
        self.add_availability(next_available_day, time(9, 0), time(12, 0))

        suggestions = suggest_next_slots(
            business=self.business,
            start_date=selected_day,
            duration_minutes=120,
            now=self.future_now,
            days_ahead=7,
            limit=2,
        )

        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0].starts_at.date(), next_available_day)
        self.assertEqual(suggestions[0].starts_at.time(), time(9, 0))

    def test_engine_scores_slots_that_compact_the_agenda(self):
        target_date = date(2026, 7, 6)
        self.add_availability(target_date, time(9, 0), time(13, 0))
        self.line_2.is_active = False
        self.line_2.save()
        self.add_appointment(aware_datetime(2026, 7, 6, 9, 0), minutes=60)

        result = get_day_availability(
            business=self.business,
            target_date=target_date,
            duration_minutes=60,
            now=self.future_now,
        )

        recommended = result.slots[0]
        self.assertEqual(recommended.starts_at.time(), time(10, 0))
        self.assertEqual(recommended.reason, "compacta_agenda")
        self.assertGreater(recommended.score, result.slots[-1].score)

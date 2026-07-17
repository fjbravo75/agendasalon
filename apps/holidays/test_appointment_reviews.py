from datetime import datetime, time, timedelta, timezone as datetime_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import (
    Appointment,
    AppointmentService,
    BusinessCalendarSettings,
    Service,
    WorkLine,
)
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.customers.models import BusinessClient
from apps.holidays import appointment_reviews
from apps.holidays.appointment_reviews import (
    acknowledge_holiday_appointment,
    current_holiday_impact_for_appointment,
    pending_holiday_appointments,
    pending_holiday_business_summaries,
)
from apps.holidays.models import (
    HolidayAppointmentReview,
    HolidaySyncRun,
    OfficialHoliday,
)
from apps.notifications.models import InternalNotification, OutboundEmail


MADRID = ZoneInfo("Europe/Madrid")


class HolidayReviewFixtureMixin:
    def setUp(self):
        super().setUp()
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600111901",
            phone="+34600111901",
            password="test-pass-123",
            full_name="Profesional Festivos",
        )
        self.other_professional = get_user_model().objects.create_user(
            normalized_phone="+34600111902",
            phone="+34600111902",
            password="test-pass-123",
            full_name="Profesional Otro Salón",
        )
        self.business, self.client_file, self.work_line = self._create_business(
            name="Salón Calendario",
            slug="salon-calendario",
            professional=self.professional,
            client_name="Ana Cliente Festivo",
            client_phone="+34610111901",
        )
        self.other_business, self.other_client, self.other_line = self._create_business(
            name="Estudio Independiente",
            slug="estudio-independiente",
            professional=self.other_professional,
            client_name="Bea Cliente Privada",
            client_phone="+34610111902",
        )
        self.target_date = timezone.localdate() + timedelta(days=45)
        self.holiday = OfficialHoliday.objects.create(
            date=self.target_date,
            name="Fiesta nacional de prueba",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=self.target_date.year,
            source_name="BOE - prueba",
            official_reference="BOE-TEST-HOLIDAY-REVIEW",
        )
        self.appointment = self._create_appointment(
            business=self.business,
            client_file=self.client_file,
            work_line=self.work_line,
            starts_at=timezone.make_aware(
                datetime.combine(self.target_date, time(10, 0)),
                MADRID,
            ),
        )
        self.other_appointment = self._create_appointment(
            business=self.other_business,
            client_file=self.other_client,
            work_line=self.other_line,
            starts_at=timezone.make_aware(
                datetime.combine(self.target_date, time(12, 0)),
                MADRID,
            ),
        )

    def _create_business(self, *, name, slug, professional, client_name, client_phone):
        business = Business.objects.create(commercial_name=name, slug=slug)
        BusinessCalendarSettings.objects.create(
            business=business,
            apply_national_holidays=True,
        )
        BusinessMembership.objects.create(business=business, user=professional)
        client_file = BusinessClient.objects.create(
            business=business,
            full_name=client_name,
            phone=client_phone,
        )
        work_line = WorkLine.objects.create(
            business=business,
            line_number=1,
            name="Línea principal",
        )
        return business, client_file, work_line

    def _create_appointment(
        self,
        *,
        business,
        client_file,
        work_line,
        starts_at,
        status=Appointment.Status.CONFIRMED,
    ):
        appointment = Appointment(
            business=business,
            business_client=client_file,
            work_line=work_line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=status,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
            service_summary_snapshot="Corte y peinado",
        )
        appointment.full_clean()
        appointment.save()
        return appointment


class HolidayAppointmentReviewServiceTests(HolidayReviewFixtureMixin, TestCase):
    def test_live_query_uses_business_timezone_at_the_utc_date_boundary(self):
        target_date = datetime(2030, 10, 12).date()
        holiday = OfficialHoliday.objects.create(
            date=target_date,
            name="Fiesta nacional en el límite UTC",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2030,
            source_name="BOE - prueba",
        )
        local_start = datetime(2030, 10, 12, 0, 30, tzinfo=MADRID)
        boundary_appointment = self._create_appointment(
            business=self.business,
            client_file=self.client_file,
            work_line=self.work_line,
            starts_at=local_start,
        )

        impacts = pending_holiday_appointments(
            business=self.business,
            year=2030,
            at=datetime(2030, 10, 11, 23, 0, tzinfo=MADRID),
        )

        self.assertEqual(
            boundary_appointment.starts_at.astimezone(datetime_timezone.utc).date(),
            datetime(2030, 10, 11).date(),
        )
        self.assertEqual([impact.appointment.pk for impact in impacts], [boundary_appointment.pk])
        self.assertEqual(impacts[0].holiday, holiday)
        self.assertEqual(impacts[0].local_starts_at.date(), target_date)

    def test_live_query_filters_status_future_and_business_setting(self):
        cancelled = self._create_appointment(
            business=self.business,
            client_file=self.client_file,
            work_line=self.work_line,
            starts_at=self.appointment.starts_at + timedelta(hours=1),
            status=Appointment.Status.CANCELLED,
        )

        impacts = pending_holiday_appointments(business=self.business)

        self.assertEqual([impact.appointment.pk for impact in impacts], [self.appointment.pk])
        self.assertNotIn(cancelled.pk, [impact.appointment.pk for impact in impacts])
        self.assertEqual(
            pending_holiday_appointments(
                business=self.business,
                at=self.appointment.starts_at,
            ),
            tuple(),
        )
        self.assertIsNone(
            current_holiday_impact_for_appointment(
                self.appointment,
                at=self.appointment.starts_at,
            )
        )
        self.assertEqual(
            pending_holiday_appointments(
                business=self.business,
                at=self.appointment.starts_at + timedelta(minutes=1),
            ),
            tuple(),
        )

        self.business.calendar_settings.apply_national_holidays = False
        self.business.calendar_settings.save(update_fields=["apply_national_holidays"])
        self.assertEqual(pending_holiday_appointments(business=self.business), tuple())

    def test_acknowledgement_is_idempotent_unique_and_removed_from_live_queue(self):
        reviewed_at = timezone.now()

        first = acknowledge_holiday_appointment(
            business=self.business,
            appointment_id=self.appointment.pk,
            reviewed_by=self.professional,
            at=reviewed_at,
        )
        second = acknowledge_holiday_appointment(
            business=self.business,
            appointment_id=self.appointment.pk,
            reviewed_by=self.professional,
            at=reviewed_at + timedelta(seconds=1),
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.review, second.review)
        self.assertEqual(first.review.reviewed_by, self.professional)
        self.assertEqual(first.review.reviewed_at, reviewed_at)
        self.assertEqual(first.review.holiday, self.holiday)
        self.assertEqual(first.review.holiday_date, self.holiday.date)
        self.assertEqual(first.review.holiday_name, self.holiday.name)
        self.assertEqual(
            HolidayAppointmentReview.objects.filter(appointment=self.appointment).count(),
            1,
        )
        self.assertEqual(pending_holiday_appointments(business=self.business), tuple())

        with self.assertRaises(IntegrityError), transaction.atomic():
            HolidayAppointmentReview.objects.create(
                appointment=self.appointment,
                holiday=self.holiday,
                holiday_date=self.holiday.date,
                holiday_name=self.holiday.name,
                reviewed_by=self.professional,
                reviewed_at=reviewed_at,
            )

    def test_review_keeps_its_snapshot_if_the_official_row_is_removed(self):
        result = acknowledge_holiday_appointment(
            business=self.business,
            appointment_id=self.appointment.pk,
            reviewed_by=self.professional,
        )

        self.holiday.delete()
        result.review.refresh_from_db()

        self.assertIsNone(result.review.holiday_id)
        self.assertEqual(result.review.holiday_date, self.target_date)
        self.assertEqual(result.review.holiday_name, "Fiesta nacional de prueba")

    def test_acknowledgement_revalidates_status_holiday_and_business_setting(self):
        self.appointment.status = Appointment.Status.CANCELLED
        self.appointment.save(update_fields=["status"])
        with self.assertRaisesMessage(ValidationError, "ya no está confirmada"):
            acknowledge_holiday_appointment(
                business=self.business,
                appointment_id=self.appointment.pk,
                reviewed_by=self.professional,
            )

        self.appointment.status = Appointment.Status.CONFIRMED
        self.appointment.save(update_fields=["status"])
        self.business.calendar_settings.apply_national_holidays = False
        self.business.calendar_settings.save(update_fields=["apply_national_holidays"])
        with self.assertRaisesMessage(ValidationError, "no aplica ahora mismo"):
            acknowledge_holiday_appointment(
                business=self.business,
                appointment_id=self.appointment.pk,
                reviewed_by=self.professional,
            )

    def test_acknowledgement_rejects_started_appointment_and_removed_holiday(self):
        for effective_at in (
            self.appointment.starts_at,
            self.appointment.starts_at + timedelta(minutes=1),
        ):
            with self.subTest(effective_at=effective_at), self.assertRaisesMessage(
                ValidationError,
                "ya ha comenzado",
            ):
                acknowledge_holiday_appointment(
                    business=self.business,
                    appointment_id=self.appointment.pk,
                    reviewed_by=self.professional,
                    at=effective_at,
                )

        self.holiday.delete()
        with self.assertRaisesMessage(ValidationError, "ya no coincide"):
            acknowledge_holiday_appointment(
                business=self.business,
                appointment_id=self.appointment.pk,
                reviewed_by=self.professional,
            )
        self.assertFalse(
            HolidayAppointmentReview.objects.filter(appointment=self.appointment).exists()
        )

    def test_acknowledgement_only_records_review_without_mutations_or_notices(self):
        appointment_state = (
            self.appointment.status,
            self.appointment.starts_at,
            self.appointment.ends_at,
            self.appointment.work_line_id,
            self.appointment.business_client_id,
        )
        counts_before = (
            Appointment.objects.count(),
            BusinessActivityEvent.objects.count(),
            InternalNotification.objects.count(),
            OutboundEmail.objects.count(),
        )

        acknowledge_holiday_appointment(
            business=self.business,
            appointment_id=self.appointment.pk,
            reviewed_by=self.professional,
        )

        self.appointment.refresh_from_db()
        self.assertEqual(
            (
                self.appointment.status,
                self.appointment.starts_at,
                self.appointment.ends_at,
                self.appointment.work_line_id,
                self.appointment.business_client_id,
            ),
            appointment_state,
        )
        self.assertEqual(
            (
                Appointment.objects.count(),
                BusinessActivityEvent.objects.count(),
                InternalNotification.objects.count(),
                OutboundEmail.objects.count(),
            ),
            counts_before,
        )

    def test_review_for_an_unrelated_date_does_not_hide_the_current_holiday(self):
        HolidayAppointmentReview.objects.create(
            appointment=self.appointment,
            holiday=None,
            holiday_date=self.target_date - timedelta(days=1),
            holiday_name="Festivo anterior",
            reviewed_by=self.professional,
            reviewed_at=timezone.now(),
        )

        impacts = pending_holiday_appointments(business=self.business)

        self.assertEqual(
            [impact.appointment.pk for impact in impacts],
            [self.appointment.pk],
        )

    def test_acknowledgement_reads_real_time_only_after_acquiring_the_mutex(self):
        lock_acquired = False
        real_calendar_lock = appointment_reviews.lock_business_calendar

        def observed_calendar_lock(business):
            nonlocal lock_acquired
            result = real_calendar_lock(business)
            lock_acquired = True
            return result

        def current_time_after_start():
            self.assertTrue(lock_acquired)
            return self.appointment.starts_at + timedelta(minutes=1)

        with patch(
            "apps.holidays.appointment_reviews.lock_business_calendar",
            side_effect=observed_calendar_lock,
        ), patch(
            "apps.holidays.appointment_reviews.timezone.now",
            side_effect=current_time_after_start,
        ), self.assertRaisesMessage(ValidationError, "ya ha comenzado"):
            acknowledge_holiday_appointment(
                business=self.business,
                appointment_id=self.appointment.pk,
                reviewed_by=self.professional,
            )

        self.assertFalse(
            HolidayAppointmentReview.objects.filter(appointment=self.appointment).exists()
        )

    def test_business_summaries_contain_aggregates_only(self):
        summaries = pending_holiday_business_summaries(year=self.target_date.year)

        self.assertEqual(
            [(item.business_name, item.appointment_count) for item in summaries],
            [("Estudio Independiente", 1), ("Salón Calendario", 1)],
        )
        self.assertFalse(hasattr(summaries[0], "client_name"))
        self.assertFalse(hasattr(summaries[0], "phone"))

    def test_business_summaries_keep_pending_businesses_without_professional_access(self):
        self.other_business.is_active = False
        self.other_business.save(update_fields=["is_active"])
        self.professional.is_active = False
        self.professional.save(update_fields=["is_active"])

        summaries = pending_holiday_business_summaries(year=self.target_date.year)
        summaries_by_business = {item.business_id: item for item in summaries}

        paused = summaries_by_business[self.other_business.pk]
        self.assertFalse(paused.business_is_active)
        self.assertTrue(paused.has_active_professional)
        self.assertEqual(paused.appointment_count, 1)

        without_professional = summaries_by_business[self.business.pk]
        self.assertTrue(without_professional.business_is_active)
        self.assertFalse(without_professional.has_active_professional)
        self.assertEqual(without_professional.appointment_count, 1)


class HolidayAppointmentReviewViewTests(HolidayReviewFixtureMixin, TestCase):
    def test_professional_queue_is_private_and_links_to_existing_management(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:professional_holiday_appointments"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Citas en festivos nacionales")
        self.assertContains(response, self.client_file.full_name)
        self.assertContains(response, "Gestionar cita")
        self.assertNotContains(response, self.other_client.full_name)
        self.assertNotContains(response, self.other_client.phone)

    def test_professional_queue_accepts_safe_methods_and_is_never_cached(self):
        self.client.force_login(self.professional)
        path = reverse("booking:professional_holiday_appointments")

        get_response = self.client.get(path)
        head_response = self.client.head(path)

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(head_response.status_code, 200)
        self.assertEqual(head_response.content, b"")
        for response in (get_response, head_response):
            self.assertIn("no-store", response.headers["Cache-Control"])
            self.assertIn("private", response.headers["Cache-Control"])

    def test_professional_queue_rejects_mutating_methods_behind_login(self):
        path = reverse("booking:professional_holiday_appointments")

        anonymous_response = self.client.post(path)

        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn(reverse("accounts:login"), anonymous_response.url)
        self.assertIn("no-store", anonymous_response.headers["Cache-Control"])
        self.assertIn("private", anonymous_response.headers["Cache-Control"])

        self.client.force_login(self.professional)
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(path)
                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.headers["Allow"], "GET, HEAD")
                self.assertIn("no-store", response.headers["Cache-Control"])
                self.assertIn("private", response.headers["Cache-Control"])

    def test_schedule_and_detail_explain_the_review_without_automatic_changes(self):
        self.client.force_login(self.professional)

        schedule_response = self.client.get(reverse("booking:professional_schedule"))
        detail_response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[self.appointment.pk])
        )

        schedule_html = " ".join(schedule_response.content.decode().split())
        self.assertIn("1 cita en festivo", schedule_html)
        self.assertContains(schedule_response, "Revisar citas")
        self.assertContains(detail_response, self.holiday.name)
        self.assertContains(detail_response, "No se ha cancelado ni movido automáticamente")
        self.assertContains(detail_response, "Buscar otra hora")
        self.assertContains(detail_response, "Confirmar que se mantiene")

    def test_holiday_rebook_cta_prefills_active_data_without_starting_a_search(self):
        active_service = Service.objects.create(
            business=self.business,
            name="Corte activo",
            duration_minutes=30,
            display_order=1,
        )
        inactive_service = Service.objects.create(
            business=self.business,
            name="Tratamiento retirado",
            duration_minutes=30,
            is_active=False,
            display_order=2,
        )
        for display_order, service in enumerate((active_service, inactive_service), start=1):
            AppointmentService.objects.create(
                appointment=self.appointment,
                service=service,
                display_order=display_order,
            )
        self.client.force_login(self.professional)

        detail_response = self.client.get(
            reverse("booking:professional_appointment_detail", args=[self.appointment.pk])
        )
        rebook_url = detail_response.context["holiday_rebook_url"]

        self.assertContains(detail_response, "prefill_from_agenda=1")
        self.assertIn(f"business_client={self.client_file.pk}", rebook_url)
        self.assertIn(f"services={active_service.pk}", rebook_url)
        self.assertNotIn(f"services={inactive_service.pk}", rebook_url)

        assistant_response = self.client.get(rebook_url)

        self.assertEqual(assistant_response.status_code, 200)
        self.assertTrue(assistant_response.context["agenda_prefill"])
        self.assertFalse(assistant_response.context["has_search"])
        self.assertFalse(assistant_response.context["form"].is_bound)
        self.assertEqual(
            str(assistant_response.context["form"]["business_client"].value()),
            str(self.client_file.pk),
        )
        self.assertEqual(
            assistant_response.context["selected_service_ids"],
            (active_service.pk,),
        )
        self.assertFalse(assistant_response.context["form"].errors)
        self.assertNotContains(assistant_response, "Selecciona al menos un servicio.")
        self.assertNotContains(assistant_response, "Falta algún dato")

    def test_professional_can_acknowledge_keep_and_the_queue_updates(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse(
                "booking:professional_holiday_appointment_acknowledge",
                args=[self.appointment.pk],
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "La cita queda revisada y continúa confirmada en ese festivo",
        )
        self.assertContains(response, "Se mantiene")
        self.assertNotContains(response, "Confirmar que se mantiene")
        review = HolidayAppointmentReview.objects.get(appointment=self.appointment)
        self.assertEqual(review.reviewed_by, self.professional)

        queue_response = self.client.get(reverse("booking:professional_holiday_appointments"))
        self.assertContains(queue_response, "Todo revisado")
        self.assertNotContains(queue_response, self.client_file.full_name)

    def test_acknowledgement_requires_post_csrf_and_own_business(self):
        self.client.force_login(self.professional)
        own_url = reverse(
            "booking:professional_holiday_appointment_acknowledge",
            args=[self.appointment.pk],
        )
        other_url = reverse(
            "booking:professional_holiday_appointment_acknowledge",
            args=[self.other_appointment.pk],
        )

        self.assertEqual(self.client.get(own_url).status_code, 405)
        self.assertEqual(self.client.post(other_url).status_code, 404)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.professional)
        self.assertEqual(csrf_client.post(own_url).status_code, 403)
        self.assertFalse(
            HolidayAppointmentReview.objects.filter(appointment=self.appointment).exists()
        )

    def test_cancellation_removes_the_appointment_from_the_live_queue(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:professional_appointment_cancel", args=[self.appointment.pk]),
            {"cancellation_reason": "Cambio acordado por el festivo nacional."},
        )

        self.assertEqual(response.status_code, 302)
        self.appointment.refresh_from_db()
        self.assertEqual(self.appointment.status, Appointment.Status.CANCELLED)
        queue_response = self.client.get(reverse("booking:professional_holiday_appointments"))
        self.assertNotContains(queue_response, self.client_file.full_name)
        self.assertContains(queue_response, "Todo revisado")

    def test_superadmin_sees_business_aggregates_without_customer_data(self):
        superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000991",
            phone="+34910000991",
            password="test-pass-123",
            full_name="Admin BOE P2",
        )
        HolidaySyncRun.objects.create(
            year=self.target_date.year,
            source_name="BOE - calendario laboral nacional",
            source_url="https://www.boe.es/diario_boe/txt.php?id=BOE-A-P2-PRIVACY",
            official_reference="BOE-A-P2-PRIVACY",
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=timezone.now() - timedelta(minutes=1),
            finished_at=timezone.now(),
            items_loaded=1,
            affected_appointments=2,
            affected_businesses=2,
        )
        self.other_business.is_active = False
        self.other_business.save(update_fields=["is_active"])
        self.professional.is_active = False
        self.professional.save(update_fields=["is_active"])
        next_year_date = self.target_date + timedelta(days=370)
        OfficialHoliday.objects.create(
            date=next_year_date,
            name="Festivo futuro de otro año",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=next_year_date.year,
            source_name="BOE - prueba",
        )
        self._create_appointment(
            business=self.business,
            client_file=self.client_file,
            work_line=self.work_line,
            starts_at=timezone.make_aware(
                datetime.combine(next_year_date, time(11, 0)),
                MADRID,
            ),
        )
        self.client.force_login(superadmin)

        response = self.client.get(
            f"{reverse('platform_settings:superadmin_platform_settings')}"
            f"?holiday_year={self.target_date.year}"
        )

        self.assertEqual(response.status_code, 200)
        response_html = " ".join(response.content.decode().split())
        self.assertIn(
            "Al terminar se detectaron 2 citas futuras en 2 negocios",
            response_html,
        )
        self.assertContains(response, "Negocios con citas por revisar")
        self.assertEqual(response.context["holiday_business_impact_total"], 3)
        self.assertContains(
            response,
            "aunque pertenezcan a un año distinto del calendario que estás consultando",
        )
        self.assertContains(response, "Negocio pausado")
        self.assertContains(response, "No hay acceso al panel profesional")
        self.assertContains(response, "Sin profesional activo")
        self.assertContains(response, "No hay acceso profesional disponible")
        self.assertContains(
            response,
            'aria-label="Negocios con citas pendientes de revisión"',
        )
        self.assertContains(response, "BOE-A-P2-PRIVACY")
        self.assertContains(response, "se abre en otra pestaña")
        self.assertContains(response, self.business.commercial_name)
        self.assertContains(response, self.other_business.commercial_name)
        self.assertNotContains(response, self.client_file.full_name)
        self.assertNotContains(response, self.client_file.phone)
        self.assertNotContains(response, self.other_client.full_name)
        self.assertNotContains(response, self.other_client.phone)

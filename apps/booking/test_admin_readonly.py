from datetime import datetime, time, timedelta

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.admin import AppointmentServiceInline
from apps.booking.models import (
    Appointment,
    AppointmentService,
    AvailabilityRule,
    BusinessCalendarSettings,
    BusinessClosure,
    Service,
    WorkLine,
)
from apps.businesses.models import Business
from apps.customers.models import BusinessClient


class BookingAdminReadOnlyTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            normalized_phone="+34910000901",
            phone="+34910000901",
            password="test-pass-123",
            full_name="Administración técnica",
        )
        self.business = Business.objects.create(
            commercial_name="Salón Admin",
            slug="salon-admin",
        )
        self.calendar_settings = BusinessCalendarSettings.objects.create(
            business=self.business,
            slot_interval_minutes=15,
        )
        self.availability_rule = AvailabilityRule.objects.create(
            business=self.business,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(18, 0),
        )
        self.work_line = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Línea 1",
        )
        self.service = Service.objects.create(
            business=self.business,
            name="Corte",
            duration_minutes=30,
        )
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.closure = BusinessClosure.objects.create(
            business=self.business,
            date_from=tomorrow,
            date_to=tomorrow,
            closure_type=BusinessClosure.ClosureType.OTHER,
        )
        client_file = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente Admin",
        )
        starts_at = timezone.make_aware(
            datetime.combine(tomorrow, time(10, 0)),
            timezone.get_current_timezone(),
        )
        self.appointment = Appointment.objects.create(
            business=self.business,
            business_client=client_file,
            work_line=self.work_line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
        )
        self.appointment_service = AppointmentService.objects.create(
            appointment=self.appointment,
            service=self.service,
            display_order=1,
            service_name_snapshot=self.service.name,
            duration_minutes_snapshot=self.service.duration_minutes,
        )
        self.objects_by_model = {
            BusinessCalendarSettings: self.calendar_settings,
            AvailabilityRule: self.availability_rule,
            WorkLine: self.work_line,
            Service: self.service,
            BusinessClosure: self.closure,
            Appointment: self.appointment,
            AppointmentService: self.appointment_service,
        }
        self.client.force_login(self.superuser)

    def _admin_url(self, model, action, *args):
        opts = model._meta
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_{action}", args=args)

    def test_operational_models_remain_visible_but_have_no_mutation_permissions(self):
        request = RequestFactory().get("/admin/")
        request.user = self.superuser

        for model, instance in self.objects_by_model.items():
            with self.subTest(model=model.__name__):
                model_admin = admin.site._registry[model]
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_change_permission(request, instance))
                self.assertFalse(model_admin.has_delete_permission(request, instance))
                self.assertEqual(model_admin.get_actions(request), {})
                self.assertEqual(
                    self.client.get(self._admin_url(model, "changelist")).status_code,
                    200,
                )
                self.assertEqual(
                    self.client.get(
                        self._admin_url(model, "change", instance.pk)
                    ).status_code,
                    200,
                )

    def test_admin_post_cannot_add_change_or_delete_appointments(self):
        original_starts_at = self.appointment.starts_at
        original_count = Appointment.objects.count()

        self.assertEqual(
            self.client.post(self._admin_url(Appointment, "add"), {}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self._admin_url(Appointment, "change", self.appointment.pk),
                {"status": Appointment.Status.CANCELLED},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self._admin_url(Appointment, "delete", self.appointment.pk),
                {"post": "yes"},
            ).status_code,
            403,
        )

        self.appointment.refresh_from_db()
        self.assertEqual(Appointment.objects.count(), original_count)
        self.assertEqual(self.appointment.status, Appointment.Status.CONFIRMED)
        self.assertEqual(self.appointment.starts_at, original_starts_at)

    def test_every_operational_model_rejects_admin_post_mutations(self):
        for model, instance in self.objects_by_model.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(
                    self.client.post(self._admin_url(model, "add"), {}).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        self._admin_url(model, "change", instance.pk),
                        {},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    self.client.post(
                        self._admin_url(model, "delete", instance.pk),
                        {"post": "yes"},
                    ).status_code,
                    403,
                )

    def test_appointment_service_inline_and_direct_admin_are_read_only(self):
        request = RequestFactory().get("/admin/")
        request.user = self.superuser
        inline = AppointmentServiceInline(Appointment, admin.site)

        self.assertFalse(inline.has_add_permission(request, self.appointment))
        self.assertFalse(inline.has_change_permission(request, self.appointment_service))
        self.assertFalse(inline.has_delete_permission(request, self.appointment_service))
        self.assertFalse(inline.can_delete)

        original_duration = self.appointment_service.duration_minutes_snapshot
        response = self.client.post(
            self._admin_url(
                AppointmentService,
                "change",
                self.appointment_service.pk,
            ),
            {"duration_minutes_snapshot": 90},
        )

        self.assertEqual(response.status_code, 403)
        self.appointment_service.refresh_from_db()
        self.assertEqual(
            self.appointment_service.duration_minutes_snapshot,
            original_duration,
        )

    def test_business_admin_is_visible_but_fully_read_only(self):
        request = RequestFactory().get("/admin/")
        request.user = self.superuser
        business_admin = admin.site._registry[Business]
        original_name = self.business.commercial_name
        original_is_active = self.business.is_active

        self.assertFalse(business_admin.has_add_permission(request))
        self.assertFalse(business_admin.has_change_permission(request, self.business))
        self.assertFalse(business_admin.has_delete_permission(request, self.business))
        self.assertEqual(business_admin.get_actions(request), {})
        self.assertEqual(
            self.client.get(self._admin_url(Business, "changelist")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                self._admin_url(Business, "change", self.business.pk)
            ).status_code,
            200,
        )

        self.assertEqual(
            self.client.post(self._admin_url(Business, "add"), {}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self._admin_url(Business, "change", self.business.pk),
                {"commercial_name": "Nombre manipulado", "is_active": ""},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                self._admin_url(Business, "delete", self.business.pk),
                {"post": "yes"},
            ).status_code,
            403,
        )

        self.business.refresh_from_db()
        self.assertTrue(Business.objects.filter(pk=self.business.pk).exists())
        self.assertEqual(self.business.commercial_name, original_name)
        self.assertEqual(self.business.is_active, original_is_active)

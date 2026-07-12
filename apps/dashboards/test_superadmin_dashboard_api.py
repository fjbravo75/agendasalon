from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment, AvailabilityRule, Service, WorkLine
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.customers.models import BusinessClient


class SuperadminDashboardApiTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000501",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600000501",
            password="test-pass-123",
            full_name="Profesional",
        )
        self.operational = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari-api-panel",
            city="Madrid",
            public_booking_enabled=True,
        )
        BusinessMembership.objects.create(
            business=self.operational,
            user=self.professional,
        )
        Service.objects.create(
            business=self.operational,
            name="Corte",
            duration_minutes=30,
        )
        self.work_line = WorkLine.objects.create(
            business=self.operational,
            line_number=1,
            name="Línea 1",
        )
        AvailabilityRule.objects.create(
            business=self.operational,
            weekday=0,
            start_time="09:00",
            end_time="14:00",
        )
        self.incomplete = Business.objects.create(
            commercial_name="Salón por configurar",
            slug="salon-por-configurar-api-panel",
            public_booking_enabled=False,
        )
        self.paused = Business.objects.create(
            commercial_name="Barbería pausada",
            slug="barberia-pausada-api-panel",
            is_active=False,
        )
        self.url = reverse("dashboards:superadmin_dashboard_data")

    def test_requires_authentication_and_superadmin_role(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.professional)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "superadmin_required")

    def test_is_read_only_and_disables_cache(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 405)

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-cache", response.headers["Cache-Control"])
        self.assertIn("Cookie", response.headers["Vary"])

    def test_returns_real_summary_and_health_by_business(self):
        self.client.force_login(self.superadmin)

        payload = self.client.get(self.url).json()

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["summary"]["businesses_total"], 3)
        self.assertEqual(payload["summary"]["businesses_operational"], 1)
        self.assertEqual(payload["summary"]["businesses_setup_pending"], 1)
        self.assertEqual(payload["summary"]["businesses_inactive"], 1)
        businesses = {item["name"]: item for item in payload["businesses"]}
        self.assertEqual(businesses["Peluquería Mari"]["health"]["code"], "operational")
        self.assertEqual(
            businesses["Salón por configurar"]["health"]["code"],
            "setup_pending",
        )
        self.assertIn(
            "Acceso profesional",
            businesses["Salón por configurar"]["health"]["missing_setup"],
        )
        self.assertEqual(businesses["Barbería pausada"]["health"]["code"], "inactive")

    def test_pending_closure_is_a_professional_task_not_a_fake_completed_appointment(self):
        client = BusinessClient.objects.create(
            business=self.operational,
            full_name="Carmen Ruiz",
            phone="600000501",
        )
        now = timezone.now()
        Appointment.objects.create(
            business=self.operational,
            business_client=client,
            work_line=self.work_line,
            starts_at=now - timedelta(hours=2),
            ends_at=now - timedelta(hours=1),
            total_duration_minutes=60,
            status=Appointment.Status.CONFIRMED,
        )
        self.client.force_login(self.superadmin)

        payload = self.client.get(self.url).json()

        self.assertEqual(payload["summary"]["pending_closure_appointments"], 1)
        self.assertEqual(payload["summary"]["businesses_with_pending_closure"], 1)
        business = next(item for item in payload["businesses"] if item["id"] == self.operational.id)
        self.assertEqual(business["counts"]["pending_closure"], 1)
        status = next(item for item in payload["appointment_statuses"] if item["code"] == "pending_closure")
        self.assertEqual(status["value"], 1)

    def test_recent_activity_omits_client_identity_and_contact_data(self):
        BusinessActivityEvent.objects.create(
            business=self.operational,
            actor_type=BusinessActivityEvent.ActorType.PROFESSIONAL,
            actor_label="Equipo",
            category=BusinessActivityEvent.Category.APPOINTMENTS,
            event_type=BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
            origin=BusinessActivityEvent.Origin.PHONE,
            summary="Cita creada para Carmen Ruiz con teléfono 600000501.",
        )
        self.client.force_login(self.superadmin)

        content = self.client.get(self.url).content.decode("utf-8")

        self.assertNotIn("Carmen Ruiz", content)
        self.assertNotIn("600000501", content)
        event = self.client.get(self.url).json()["recent_activity"][0]
        self.assertEqual(event["event_label"], "Cita creada")
        self.assertEqual(event["business"]["name"], "Peluquería Mari")
        self.assertNotIn("summary", event)

    def test_activity_series_has_fourteen_contiguous_days(self):
        self.client.force_login(self.superadmin)

        series = self.client.get(self.url).json()["activity_series"]

        self.assertEqual(len(series), 14)
        self.assertEqual(series[-1]["date"], timezone.localdate().isoformat())
        self.assertEqual(series[0]["date"], (timezone.localdate() - timedelta(days=13)).isoformat())

    def test_query_budget_does_not_grow_with_the_number_of_businesses(self):
        Business.objects.bulk_create(
            [
                Business(
                    commercial_name=f"Negocio de carga {index}",
                    slug=f"negocio-carga-{index}",
                )
                for index in range(12)
            ]
        )
        self.client.force_login(self.superadmin)

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(queries),
            12,
            f"El dashboard ha superado su presupuesto de consultas: {len(queries)}",
        )


class SuperadminDashboardReactViewTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000502",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600000502",
            password="test-pass-123",
            full_name="Profesional",
        )
        self.url = reverse("dashboards:superadmin_home")

    def test_mounts_the_route_specific_react_island_for_superadmin(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="superadmin-dashboard-root"')
        self.assertContains(response, 'id="superadmin-dashboard-config"')
        self.assertContains(response, "react/dashboard.css")
        self.assertContains(response, "react/dashboard.js")
        self.assertContains(response, reverse("dashboards:superadmin_dashboard_data"))
        self.assertNotContains(response, "Abrir reserva")

    def test_rejects_professional_before_rendering_the_island(self):
        self.client.force_login(self.professional)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, "superadmin-dashboard-root", status_code=403)

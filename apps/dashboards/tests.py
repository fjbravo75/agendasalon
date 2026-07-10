from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment, AvailabilityRule, Service, WorkLine
from apps.businesses.models import Business, BusinessMembership
from apps.customers.models import BusinessClient


class DashboardAccessTests(TestCase):
    def test_professional_home_requires_login(self):
        response = self.client.get(reverse("dashboards:professional_home"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_professional_home_requires_active_business_membership(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600111001",
            password="test-pass-123",
            full_name="Profesional sin negocio",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("dashboards:professional_home"))

        self.assertRedirects(response, reverse("accounts:no_business"))

    def test_professional_home_loads_for_active_membership(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600111002",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )
        BusinessMembership.objects.create(business=business, user=user)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboards:professional_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peluquería Mari")
        self.assertContains(response, "Agenda de hoy")
        self.assertContains(response, "Huecos recomendados")
        self.assertContains(response, "Preparado para agendar")
        self.assertContains(response, "Salón preparado")
        self.assertNotContains(response, "6/3")
        self.assertNotContains(response, "Un resumen rapido")

    def test_professional_home_shows_operational_day_context(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600111005",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )
        BusinessMembership.objects.create(business=business, user=user)
        service = Service.objects.create(
            business=business,
            name="Corte",
            duration_minutes=30,
            price_amount="18.00",
            is_active=True,
        )
        work_line = WorkLine.objects.create(
            business=business,
            line_number=1,
            name="Linea 1",
            is_active=True,
        )
        today = timezone.localdate()
        AvailabilityRule.objects.create(
            business=business,
            weekday=today.weekday(),
            start_time=time(9, 0),
            end_time=time(14, 0),
            is_active=True,
        )
        client = BusinessClient.objects.create(
            business=business,
            full_name="Carmen Ruiz",
            phone="600111203",
        )
        starts_at = timezone.make_aware(datetime.combine(today, time(10, 0)))
        Appointment.objects.create(
            business=business,
            business_client=client,
            work_line=work_line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=service.duration_minutes),
            total_duration_minutes=service.duration_minutes,
            status=Appointment.Status.CONFIRMED,
            service_summary_snapshot=service.name,
        )
        self.client.force_login(user)

        test_now = timezone.make_aware(datetime.combine(today, time(8, 0)))
        with patch("apps.dashboards.views.timezone.now", return_value=test_now):
            response = self.client.get(reverse("dashboards:professional_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Listo para agendar")
        self.assertContains(response, "Salón preparado")
        self.assertContains(response, "Carmen Ruiz")
        self.assertContains(response, "Corte")
        self.assertContains(response, "Linea 1")
        self.assertContains(response, "Primeras opciones para Corte")

    def test_professional_home_empty_day_is_actionable(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600111006",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )
        BusinessMembership.objects.create(business=business, user=user)
        Service.objects.create(
            business=business,
            name="Corte",
            duration_minutes=30,
            price_amount="18.00",
            is_active=True,
        )
        WorkLine.objects.create(
            business=business,
            line_number=1,
            name="Linea 1",
            is_active=True,
        )
        today = timezone.localdate()
        AvailabilityRule.objects.create(
            business=business,
            weekday=today.weekday(),
            start_time=time(9, 0),
            end_time=time(14, 0),
            is_active=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("dashboards:professional_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La agenda está despejada.")
        self.assertContains(response, "Libre hoy")
        self.assertContains(response, "Crear cita")
        self.assertNotContains(response, "Sin citas en esta línea para hoy.")

    def test_professional_home_surfaces_past_confirmed_appointments(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600111007",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )
        BusinessMembership.objects.create(business=business, user=user)
        Service.objects.create(
            business=business,
            name="Corte",
            duration_minutes=30,
            price_amount="18.00",
            is_active=True,
        )
        work_line = WorkLine.objects.create(
            business=business,
            line_number=1,
            name="Línea 1",
            is_active=True,
        )
        today = timezone.localdate()
        AvailabilityRule.objects.create(
            business=business,
            weekday=today.weekday(),
            start_time=time(9, 0),
            end_time=time(14, 0),
            is_active=True,
        )
        business_client = BusinessClient.objects.create(
            business=business,
            full_name="Carmen Ruiz",
            phone="600111203",
        )
        starts_at = timezone.now() - timedelta(days=1, hours=1)
        Appointment.objects.create(
            business=business,
            business_client=business_client,
            work_line=work_line,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            service_summary_snapshot="Corte",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("dashboards:professional_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1 por cerrar")
        self.assertContains(response, "Citas pasadas aún confirmadas")
        self.assertContains(response, "Carmen Ruiz")
        self.assertContains(response, "Con tareas")

    def test_superadmin_home_rejects_professional(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600111003",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("dashboards:superadmin_home"))

        self.assertEqual(response.status_code, 403)

    def test_superadmin_home_loads_for_superuser(self):
        user = get_user_model().objects.create_superuser(
            normalized_phone="+34600111004",
            password="test-pass-123",
            full_name="Vera Admin",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("dashboards:superadmin_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Negocios y actividad")
        self.assertContains(response, "Estado por negocio")
        self.assertContains(response, "Actividad reciente")

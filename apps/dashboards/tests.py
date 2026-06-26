from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.businesses.models import Business, BusinessMembership


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
            commercial_name="Peluqueria Mari",
            slug="peluqueria-mari",
        )
        BusinessMembership.objects.create(business=business, user=user)
        self.client.force_login(user)

        response = self.client.get(reverse("dashboards:professional_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peluqueria Mari")

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
        self.assertContains(response, "Salud del SaaS")

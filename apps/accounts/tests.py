from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.accounts.forms import PhoneAuthenticationForm
from apps.businesses.models import Business, BusinessMembership


class PhoneAuthenticationFormTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            normalized_phone="+34600111001",
            password="test-pass-123",
            full_name="Mari Profesional",
        )

    def test_form_authenticates_with_local_phone_format(self):
        form = PhoneAuthenticationForm(
            data={
                "username": "600 111 001",
                "password": "test-pass-123",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.get_user(), self.user)
        self.assertEqual(form.cleaned_data["username"], "+34600111001")

    def test_form_rejects_invalid_phone_without_leaking_user_state(self):
        form = PhoneAuthenticationForm(
            data={
                "username": "111",
                "password": "test-pass-123",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Telefono o contrasena no validos.", form.non_field_errors())


class LoginRoutingTests(TestCase):
    def test_superadmin_login_redirects_to_superadmin_dashboard(self):
        get_user_model().objects.create_superuser(
            normalized_phone="+34600111001",
            password="test-pass-123",
            full_name="Vera Admin",
        )

        response = self.client.post(
            reverse("accounts:login"),
            {"username": "600 111 001", "password": "test-pass-123"},
        )

        self.assertRedirects(response, reverse("dashboards:superadmin_home"))

    def test_professional_login_redirects_to_professional_home(self):
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

        response = self.client.post(
            reverse("accounts:login"),
            {"username": "600 111 002", "password": "test-pass-123"},
        )

        self.assertRedirects(response, reverse("dashboards:professional_home"))

    def test_user_without_business_redirects_to_no_business_page(self):
        get_user_model().objects.create_user(
            normalized_phone="+34600111003",
            password="test-pass-123",
            full_name="Profesional sin negocio",
        )

        response = self.client.post(
            reverse("accounts:login"),
            {"username": "600 111 003", "password": "test-pass-123"},
        )

        self.assertRedirects(response, reverse("accounts:no_business"))

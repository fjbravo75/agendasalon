from unittest.mock import patch

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
        self.assertIn("Teléfono o contraseña no válidos.", form.non_field_errors())


class LoginPageTemplateTests(TestCase):
    def test_login_page_uses_private_editorial_copy(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, "AgendaSalon - Acceso privado")
        self.assertContains(response, "Entrar en AgendaSalon")
        self.assertContains(response, "Acceso privado para cuentas registradas.")
        self.assertContains(response, "Agenda clara")
        self.assertContains(response, f'href="{reverse("accounts:login")}"')
        self.assertContains(response, "TELÉFONO")
        self.assertContains(response, "CONTRASEÑA")
        self.assertNotContains(response, "Entrar en mi agenda")
        self.assertNotContains(response, "¿Has olvidado")
        self.assertNotContains(response, "MVP")
        self.assertNotContains(response, "Reservas online")

    def test_invalid_login_keeps_private_editorial_context(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "600 000 000", "password": "clave-no-valida"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Teléfono o contraseña no válidos.")
        self.assertContains(response, "Entrar en AgendaSalon")
        self.assertNotContains(response, "¿Has olvidado")
        self.assertNotContains(response, "MVP")

    def test_repeated_invalid_private_login_is_rate_limited(self):
        responses = [
            self.client.post(
                reverse("accounts:login"),
                {"username": "600 000 000", "password": "clave-no-valida"},
            )
            for _ in range(5)
        ]
        with patch("apps.accounts.forms.authenticate") as authenticate_mock:
            responses.append(
                self.client.post(
                    reverse("accounts:login"),
                    {"username": "600 000 000", "password": "clave-no-valida"},
                )
            )

        authenticate_mock.assert_not_called()
        self.assertEqual(responses[-2].status_code, 429)
        self.assertEqual(responses[-1].status_code, 429)
        self.assertContains(
            responses[-1],
            "Demasiados intentos. Espera unos minutos antes de volver a intentarlo.",
            status_code=429,
        )

    def test_equivalent_phone_formats_cannot_bypass_the_login_limit(self):
        phones = [
            "600 000 000",
            "+34 600 000 000",
            "600000000",
            "+34600000000",
            "600-000-000",
        ]

        responses = [
            self.client.post(
                reverse("accounts:login"),
                {"username": phone, "password": "clave-no-valida"},
            )
            for phone in phones
        ]

        self.assertEqual(responses[-1].status_code, 429)


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
            commercial_name="Peluquería Mari",
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


class LogoutFlowTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000001",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )
        Business.objects.create(commercial_name="Peluquería Mari", slug="peluqueria-mari")
        Business.objects.create(commercial_name="Barbería Norte", slug="barberia-norte")

    def test_private_logout_ends_on_dedicated_confirmation(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(reverse("accounts:logout"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], reverse("accounts:logged_out"))
        self.assertContains(response, "Has salido de AgendaSalon")
        self.assertContains(response, "Volver a entrar")
        self.assertNotContains(response, "Peluquería Mari")
        self.assertNotContains(response, "Barbería Norte")
        self.assertNotContains(response, "¿A dónde quieres entrar?")

    def test_logout_requires_post(self):
        self.client.force_login(self.superadmin)

        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)

    def test_authenticated_user_does_not_see_logged_out_confirmation(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("accounts:logged_out"))

        self.assertRedirects(response, reverse("dashboards:superadmin_home"))

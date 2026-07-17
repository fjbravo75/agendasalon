import importlib
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.forms import PhoneAuthenticationForm
from apps.accounts.tokens import professional_email_verification_token_generator
from apps.businesses.models import Business, BusinessMembership, PlatformSettings


class DemoAccountMigrationTests(TestCase):
    def test_known_demo_accounts_remain_verified_after_migration(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34910000999",
            full_name="Demo migrada",
            email="mari@agendasalon.local",
            password="DemoAgendaSalon2026!",
            email_verification_required=True,
        )
        migration = importlib.import_module(
            "apps.accounts.migrations.0005_verify_demo_seed_accounts"
        )

        migration.verify_demo_seed_accounts(importlib.import_module("django.apps").apps, None)

        user.refresh_from_db()
        self.assertIsNotNone(user.email_verified_at)
        self.assertFalse(user.email_verification_required)


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
    def test_login_uses_short_canonical_route_and_keeps_legacy_redirect(self):
        self.assertEqual(reverse("accounts:login"), "/entrar/")

        response = self.client.get("/cuenta/entrar/?next=/profesional/")

        self.assertRedirects(
            response,
            "/entrar/?next=/profesional/",
            fetch_redirect_response=False,
        )

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
        self.assertContains(response, "agendasalon-internal-login-bg.webp")

    def test_login_page_uses_platform_selected_image(self):
        PlatformSettings.objects.create(
            login_image_preset=PlatformSettings.LoginImagePreset.BARBERSHOP
        )

        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, "customer-login-barberia-norte-bg-v2.webp")
        self.assertNotContains(response, "--internal-login-bg: url('/static/img/agendasalon")

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

    def test_temporary_professional_login_requires_personal_password_first(self):
        user = get_user_model().objects.create_user(
            normalized_phone="+34600111004",
            password="TemporalAgendaSalon2026!",
            full_name="Profesional con clave temporal",
            password_change_required=True,
        )
        business = Business.objects.create(
            commercial_name="Salón Temporal",
            slug="salon-temporal",
        )
        BusinessMembership.objects.create(business=business, user=user)

        response = self.client.post(
            reverse("accounts:login"),
            {
                "username": "600 111 004",
                "password": "TemporalAgendaSalon2026!",
            },
        )

        security_url = reverse("accounts:security")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{security_url}?next=%2Fprofesional%2F",
        )


class AccountSecurityTests(TestCase):
    old_password = "TemporalAgendaSalon2026!"
    new_password = "CuentaPersonal2026!Segura"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            normalized_phone="+34600111999",
            phone="600 111 999",
            password=self.old_password,
            full_name="Laura Profesional",
            password_change_required=True,
        )
        self.business = Business.objects.create(
            commercial_name="Salón Seguridad",
            slug="salon-seguridad",
            professional_theme=Business.ProfessionalTheme.DARK,
        )
        BusinessMembership.objects.create(business=self.business, user=self.user)

    def _payload(self, **overrides):
        payload = {
            "old_password": self.old_password,
            "new_password1": self.new_password,
            "new_password2": self.new_password,
        }
        payload.update(overrides)
        return payload

    def test_security_page_requires_authentication(self):
        response = self.client.get(reverse("accounts:security"))

        self.assertRedirects(
            response,
            f'{reverse("accounts:login")}?next={reverse("accounts:security")}',
        )

    def test_required_change_blocks_the_operational_product(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboards:professional_home"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f'{reverse("accounts:security")}?next=%2Fprofesional%2F',
        )

    def test_required_page_uses_dark_theme_and_only_exposes_logout_navigation(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("accounts:security"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crea tu contraseña personal")
        self.assertContains(response, "Contraseña temporal")
        self.assertContains(response, "theme-dark")
        self.assertContains(response, ">Salir</button>")
        self.assertNotContains(response, ">Resumen</a>")
        self.assertNotContains(response, ">Agenda</a>")

    def test_wrong_current_password_is_rejected(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:security"),
            self._payload(old_password="NoEsLaTemporal2026!"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La contraseña actual no es correcta.")
        self.user.refresh_from_db()
        self.assertTrue(self.user.password_change_required)

    def test_same_password_is_rejected(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:security"),
            self._payload(
                new_password1=self.old_password,
                new_password2=self.old_password,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "La nueva contraseña debe ser diferente de la actual.",
        )

    def test_weak_and_mismatched_passwords_are_rejected(self):
        self.client.force_login(self.user)

        weak_response = self.client.post(
            reverse("accounts:security"),
            self._payload(new_password1="12345678", new_password2="12345678"),
        )
        mismatch_response = self.client.post(
            reverse("accounts:security"),
            self._payload(new_password2="OtraCuentaPersonal2026!"),
        )

        self.assertContains(weak_response, "La contraseña es demasiado corta")
        self.assertContains(
            mismatch_response,
            "Las dos contraseñas nuevas no coinciden.",
        )

    def test_successful_required_change_keeps_current_session_and_clears_gate(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:security"),
            {**self._payload(), "next": reverse("accounts:no_business")},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:no_business"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.password_change_required)
        self.assertFalse(self.user.check_password(self.old_password))
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertEqual(
            self.client.get(reverse("accounts:security")).status_code,
            200,
        )

    def test_password_change_invalidates_other_sessions(self):
        other_client = Client()
        self.client.force_login(self.user)
        other_client.force_login(self.user)

        self.client.post(reverse("accounts:security"), self._payload())
        other_response = other_client.get(reverse("accounts:security"))

        self.assertEqual(other_response.status_code, 302)
        self.assertTrue(other_response.url.startswith(reverse("accounts:login")))
        self.assertEqual(self.client.get(reverse("accounts:security")).status_code, 200)

    def test_unsafe_next_url_is_not_used_after_change(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("accounts:security"),
            {**self._payload(), "next": "https://example.com/steal-session"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboards:professional_home"))

    def test_superadmin_can_change_password_voluntarily(self):
        superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000999",
            password=self.old_password,
            full_name="Admin AgendaSalon",
        )
        PlatformSettings.objects.create(
            admin_theme=PlatformSettings.AdminTheme.DARK,
            updated_by=superadmin,
        )
        self.client.force_login(superadmin)

        page = self.client.get(reverse("accounts:security"))
        response = self.client.post(reverse("accounts:security"), self._payload())

        self.assertContains(page, "Seguridad de la cuenta")
        self.assertContains(page, "Mi cuenta")
        self.assertContains(page, "theme-dark")
        self.assertRedirects(response, reverse("accounts:security"))
        superadmin.refresh_from_db()
        self.assertTrue(superadmin.check_password(self.new_password))


class AccountReadinessFlowTests(TestCase):
    old_password = "TemporalAgendaSalon2026!"
    new_password = "CuentaPersonal2026!Segura"

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            normalized_phone="+34600111888",
            phone="600 111 888",
            email="profesional@example.com",
            password=self.old_password,
            full_name="Profesional con dos pasos pendientes",
            password_change_required=True,
            email_verification_required=True,
        )
        self.client.force_login(self.user)

    def test_password_then_email_then_original_destination_without_redirect_loop(self):
        destination = reverse("accounts:no_business")
        security_url = reverse("accounts:security")
        email_url = reverse("accounts:email")

        first_gate = self.client.get(destination)

        self.assertEqual(first_gate.status_code, 302)
        self.assertEqual(first_gate.url, f"{security_url}?next=%2Fcuenta%2Fsin-negocio%2F")
        self.assertEqual(self.client.get(first_gate.url).status_code, 200)

        password_changed = self.client.post(
            security_url,
            {
                "old_password": self.old_password,
                "new_password1": self.new_password,
                "new_password2": self.new_password,
                "next": destination,
            },
        )

        self.assertEqual(password_changed.status_code, 302)
        self.assertEqual(password_changed.url, destination)
        self.user.refresh_from_db()
        self.assertFalse(self.user.password_change_required)
        self.assertTrue(self.user.email_verification_required)

        second_gate = self.client.get(destination)

        self.assertEqual(second_gate.status_code, 302)
        self.assertEqual(second_gate.url, f"{email_url}?next=%2Fcuenta%2Fsin-negocio%2F")
        self.assertEqual(self.client.get(second_gate.url).status_code, 200)

        self.user.refresh_from_db()
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = professional_email_verification_token_generator.make_token(self.user)
        verification_url = reverse("accounts:professional_email_verify", args=[uid, token])
        confirmation = self.client.get(verification_url)

        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "Confirmar mi correo")
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verification_required)
        self.assertIsNone(self.user.email_verified_at)

        verified = self.client.post(verification_url)

        self.assertEqual(verified.status_code, 302)
        self.assertEqual(verified.url, destination)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verification_required)
        self.assertIsNotNone(self.user.email_verified_at)
        self.assertEqual(self.client.get(destination).status_code, 200)


class ProfessionalTokenResponseSecurityTests(TestCase):
    password = "CuentaPersonal2026!Segura"

    def _user_and_token_path(
        self,
        *,
        is_active,
        phone,
        token_generator=default_token_generator,
    ):
        user = get_user_model().objects.create_user(
            normalized_phone=phone,
            full_name="Profesional con enlace privado",
            email=f"{phone[-4:]}@example.test",
            password=None if not is_active else self.password,
            is_active=is_active,
            email_verification_required=True,
        )
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = token_generator.make_token(user)
        return user, uid, token

    def test_activation_form_protects_token_and_accepts_real_csrf_post(self):
        user, uid, token = self._user_and_token_path(
            is_active=False,
            phone="+34600111901",
        )
        path = reverse("accounts:professional_activate", args=[uid, token])
        csrf_client = Client(enforce_csrf_checks=True)

        page = csrf_client.get(path, secure=True)

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Referrer-Policy"], "strict-origin")
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        csrf_token = csrf_client.cookies["csrftoken"].value

        invalid = csrf_client.post(
            path,
            {
                "csrfmiddlewaretoken": csrf_token,
                "new_password1": self.password,
                "new_password2": "UnaClaveDistinta2026!",
            },
            secure=True,
            HTTP_ORIGIN="https://testserver",
        )

        self.assertEqual(invalid.status_code, 200)
        self.assertEqual(invalid.headers["Referrer-Policy"], "strict-origin")
        self.assertEqual(invalid.headers["Cache-Control"], "no-store")

        activated = csrf_client.post(
            path,
            {
                "csrfmiddlewaretoken": csrf_token,
                "new_password1": self.password,
                "new_password2": self.password,
            },
            secure=True,
            HTTP_ORIGIN="https://testserver",
        )

        self.assertEqual(activated.status_code, 302)
        self.assertEqual(activated.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(activated.headers["Cache-Control"], "no-store")
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password(self.password))

    def test_activation_csrf_failure_never_refers_or_caches_the_token_path(self):
        _, uid, token = self._user_and_token_path(
            is_active=False,
            phone="+34600111904",
        )
        path = reverse("accounts:professional_activate", args=[uid, token])
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            path,
            {
                "new_password1": self.password,
                "new_password2": self.password,
            },
            secure=True,
            HTTP_ORIGIN="https://testserver",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_invalid_activation_page_never_sends_a_referrer(self):
        response = self.client.get(
            reverse("accounts:professional_activate", args=["invalid", "invalid"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_email_verification_get_is_read_only_and_post_requires_csrf(self):
        user, uid, token = self._user_and_token_path(
            is_active=True,
            phone="+34600111902",
            token_generator=professional_email_verification_token_generator,
        )
        path = reverse("accounts:professional_email_verify", args=[uid, token])
        csrf_client = Client(enforce_csrf_checks=True)

        page = csrf_client.get(path, secure=True)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Confirmar mi correo")
        self.assertEqual(page.headers["Referrer-Policy"], "strict-origin")
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)
        self.assertTrue(user.email_verification_required)

        rejected = csrf_client.post(
            path,
            secure=True,
            HTTP_ORIGIN="https://testserver",
        )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(rejected.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(rejected.headers["Cache-Control"], "no-store")
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

        csrf_token = csrf_client.cookies["csrftoken"].value
        verified = csrf_client.post(
            path,
            {"csrfmiddlewaretoken": csrf_token},
            secure=True,
            HTTP_ORIGIN="https://testserver",
        )

        self.assertEqual(verified.status_code, 200)
        self.assertContains(verified, "Correo verificado")
        self.assertEqual(verified.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(verified.headers["Cache-Control"], "no-store")
        user.refresh_from_db()
        self.assertIsNotNone(user.email_verified_at)
        self.assertFalse(user.email_verification_required)

        replay = csrf_client.get(path)

        self.assertEqual(replay.status_code, 410)
        self.assertEqual(replay.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(replay.headers["Cache-Control"], "no-store")

    def test_email_verification_token_survives_login_and_redirect_is_protected(self):
        user, uid, token = self._user_and_token_path(
            is_active=True,
            phone="+34600111903",
            token_generator=professional_email_verification_token_generator,
        )
        self.client.force_login(user)
        path = reverse("accounts:professional_email_verify", args=[uid, token])

        page = self.client.get(path)

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Referrer-Policy"], "strict-origin")
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

        response = self.client.post(path)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_email_verification_head_is_read_only_and_protected(self):
        user, uid, token = self._user_and_token_path(
            is_active=True,
            phone="+34600111905",
            token_generator=professional_email_verification_token_generator,
        )
        path = reverse("accounts:professional_email_verify", args=[uid, token])

        response = self.client.head(path)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.content, b"")
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)
        self.assertTrue(user.email_verification_required)

    def test_email_verification_rejects_other_methods_without_exposing_the_token_path(self):
        user, uid, token = self._user_and_token_path(
            is_active=True,
            phone="+34600111906",
            token_generator=professional_email_verification_token_generator,
        )
        path = reverse("accounts:professional_email_verify", args=[uid, token])

        response = self.client.put(path)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["Allow"], "GET, HEAD, POST")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)
        self.assertTrue(user.email_verification_required)


class LogoutFlowTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000001",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600111001",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        self.dark_business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            professional_theme=Business.ProfessionalTheme.DARK,
        )
        BusinessMembership.objects.create(
            business=self.dark_business,
            user=self.professional,
        )
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

    def test_professional_logout_keeps_dark_theme_on_confirmation(self):
        self.client.force_login(self.professional)

        response = self.client.post(reverse("accounts:logout"), follow=True)

        self.assertContains(response, "theme-dark")
        self.assertEqual(
            self.client.session["logged_out_theme"],
            Business.ProfessionalTheme.DARK,
        )

    def test_superadmin_logout_keeps_platform_theme_on_confirmation(self):
        PlatformSettings.objects.create(
            admin_theme=PlatformSettings.AdminTheme.DARK,
            updated_by=self.superadmin,
        )
        self.client.force_login(self.superadmin)

        response = self.client.post(reverse("accounts:logout"), follow=True)

        self.assertContains(response, "theme-dark")
        self.assertEqual(
            self.client.session["logged_out_theme"],
            PlatformSettings.AdminTheme.DARK,
        )

    def test_authenticated_user_does_not_see_logged_out_confirmation(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("accounts:logged_out"))

        self.assertRedirects(response, reverse("dashboards:superadmin_home"))

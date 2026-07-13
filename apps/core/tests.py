from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SecurityThrottle
from apps.core.security_throttle import (
    ThrottleLimit,
    request_ip,
    reserve_throttle_attempts,
    settle_successful_throttle,
    throttle_key_digest,
)


class RootRoutingTests(TestCase):
    def test_anonymous_root_redirects_to_internal_login(self):
        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("accounts:login"))

    def test_csrf_failure_uses_product_copy_without_exposing_the_reason(self):
        csrf_client = self.client_class(enforce_csrf_checks=True)

        response = csrf_client.post(
            reverse("accounts:login"),
            {"username": "600111001", "password": "clave-no-valida"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "No hemos podido validar el formulario.",
            status_code=403,
        )
        self.assertNotContains(response, "Reason given for failure", status_code=403)

    def test_product_responses_enforce_a_strict_script_policy(self):
        response = self.client.get(reverse("accounts:login"))

        policy = response["Content-Security-Policy"]
        directives = {
            part.strip().split(" ", 1)[0]: part.strip()
            for part in policy.split(";")
            if part.strip()
        }
        self.assertEqual(directives["default-src"], "default-src 'self'")
        self.assertEqual(directives["script-src"], "script-src 'self'")
        self.assertEqual(directives["script-src-attr"], "script-src-attr 'none'")
        self.assertEqual(directives["object-src"], "object-src 'none'")
        self.assertEqual(directives["frame-ancestors"], "frame-ancestors 'none'")
        self.assertNotIn("'unsafe-inline'", directives["script-src"])
        self.assertEqual(response["Cross-Origin-Resource-Policy"], "same-origin")
        self.assertIn("camera=()", response["Permissions-Policy"])
        self.assertIn("microphone=()", response["Permissions-Policy"])

    def test_django_admin_receives_only_its_required_script_exception(self):
        response = self.client.get(reverse("admin:login"))

        policy = response["Content-Security-Policy"]
        directives = {
            part.strip().split(" ", 1)[0]: part.strip()
            for part in policy.split(";")
            if part.strip()
        }
        self.assertIn("'unsafe-inline'", directives["script-src"])
        self.assertEqual(directives["script-src-attr"], "script-src-attr 'none'")
        self.assertEqual(directives["object-src"], "object-src 'none'")


class DjangoAdminAccessTests(TestCase):
    def test_professional_without_staff_flag_cannot_enter_django_admin(self):
        professional = get_user_model().objects.create_user(
            normalized_phone="+34600111991",
            password="test-pass-123",
            full_name="Profesional sin acceso técnico",
        )
        self.client.force_login(professional)

        response = self.client.get(reverse("admin:index"))

        self.assertRedirects(
            response,
            f'{reverse("admin:login")}?next={reverse("admin:index")}',
        )

    def test_technical_staff_needs_explicit_model_permissions(self):
        technical_staff = get_user_model().objects.create_user(
            normalized_phone="+34600111992",
            password="test-pass-123",
            full_name="Soporte técnico limitado",
            is_staff=True,
        )
        self.client.force_login(technical_staff)

        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:accounts_user_changelist")).status_code,
            403,
        )

    def test_django_superuser_can_access_registered_models(self):
        technical_superuser = get_user_model().objects.create_superuser(
            normalized_phone="+34600111993",
            password="test-pass-123",
            full_name="Administración técnica",
        )
        self.client.force_login(technical_superuser)

        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin:accounts_user_changelist")).status_code,
            200,
        )


class SecurityThrottleTests(TestCase):
    def test_successful_reservation_preserves_attempts_reserved_after_it(self):
        limits = (
            ThrottleLimit("test_subject", "600111222", 5, 900),
            ThrottleLimit("test_ip", "203.0.113.30", 30, 900),
        )
        successful_reservation = reserve_throttle_attempts(limits=limits)
        reserve_throttle_attempts(limits=limits)

        settle_successful_throttle(
            successful_reservation,
            reset_scopes={"test_subject"},
        )

        subject = SecurityThrottle.objects.get(
            scope="test_subject",
            key_digest=throttle_key_digest("600111222"),
        )
        ip_limit = SecurityThrottle.objects.get(
            scope="test_ip",
            key_digest=throttle_key_digest("203.0.113.30"),
        )
        self.assertEqual(subject.attempts, 1)
        self.assertEqual(ip_limit.attempts, 1)

    def test_untrusted_client_cannot_spoof_its_ip_with_forwarded_for(self):
        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="203.0.113.10",
            HTTP_X_FORWARDED_FOR="198.51.100.20",
        )

        self.assertEqual(request_ip(request), "203.0.113.10")

    @override_settings(TRUSTED_PROXY_IPS={"127.0.0.1", "10.0.0.2"})
    def test_trusted_proxy_chain_uses_the_nearest_untrusted_address(self):
        request = RequestFactory().get(
            "/",
            REMOTE_ADDR="127.0.0.1",
            HTTP_X_FORWARDED_FOR="198.51.100.99, 203.0.113.30, 10.0.0.2",
        )

        self.assertEqual(request_ip(request), "203.0.113.30")

    def test_prune_command_removes_only_stale_throttle_rows(self):
        now = timezone.now()
        stale = SecurityThrottle.objects.create(
            scope="test",
            key_digest="a" * 64,
            attempts=1,
            window_started_at=now - timedelta(days=40),
            last_attempt_at=now - timedelta(days=40),
        )
        recent = SecurityThrottle.objects.create(
            scope="test",
            key_digest="b" * 64,
            attempts=1,
            window_started_at=now,
            last_attempt_at=now,
        )
        output = StringIO()

        call_command("prune_security_throttles", days=30, stdout=output)

        self.assertFalse(SecurityThrottle.objects.filter(pk=stale.pk).exists())
        self.assertTrue(SecurityThrottle.objects.filter(pk=recent.pk).exists())
        self.assertIn("Contadores eliminados: 1.", output.getvalue())

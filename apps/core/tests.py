from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core.models import SecurityThrottle
from apps.core.security_throttle import request_ip


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


class SecurityThrottleTests(TestCase):
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

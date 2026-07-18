from datetime import date, timedelta
from uuid import UUID

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.models import DemoRefreshReceipt, DemoRefreshRequest, SecurityThrottle
from apps.dashboards.demo_refresh import demo_refresh_snapshot


FEATURES_ON = {
    "AGENDA_PLATFORM_LEGAL_DEMO": True,
    "AGENDA_MANUAL_DEMO_REFRESH_ENABLED": True,
    "AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED": False,
}


@override_settings(**FEATURES_ON)
class SuperadminDemoRefreshViewTests(TestCase):
    def setUp(self):
        self.password = "ClaveSeguraAgendaSalon2026!"
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000777",
            password=self.password,
            full_name="Admin AgendaSalon",
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600000777",
            password=self.password,
            full_name="Profesional",
        )
        self.url = reverse("dashboards:superadmin_demo_refresh")
        self.continuity_url = reverse("dashboards:superadmin_continuity")

    def _valid_payload(self):
        return {
            "current_password": self.password,
            "confirmation_phrase": "REGENERAR DEMO",
            "destructive_scope_confirmed": "on",
        }

    def test_get_and_head_never_create_a_request(self):
        self.client.force_login(self.superadmin)

        get_response = self.client.get(self.url)
        head_response = self.client.head(self.url)

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(head_response.status_code, 200)
        self.assertContains(get_response, "Revisar regeneración")
        self.assertContains(get_response, "REGENERAR DEMO")
        self.assertFalse(DemoRefreshRequest.objects.exists())

    def test_route_requires_authentication_active_superadmin_and_feature(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

        self.client.force_login(self.professional)
        self.assertEqual(self.client.get(self.url).status_code, 403)

        self.superadmin.is_active = False
        self.superadmin.save(update_fields=("is_active",))
        self.client.force_login(self.superadmin)
        self.assertEqual(self.client.get(self.url).status_code, 302)

        self.superadmin.is_active = True
        self.superadmin.save(update_fields=("is_active",))
        self.client.force_login(self.superadmin)
        with override_settings(AGENDA_MANUAL_DEMO_REFRESH_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_disabled_feature_hides_the_continuity_action(self):
        self.client.force_login(self.superadmin)
        with override_settings(AGENDA_MANUAL_DEMO_REFRESH_ENABLED=False):
            response = self.client.get(self.continuity_url)
        self.assertNotContains(response, "Revisar regeneración")

    def test_valid_post_stores_only_minimal_hashed_request_data(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(
            self.url,
            self._valid_payload(),
            REMOTE_ADDR="203.0.113.44",
        )

        self.assertRedirects(response, self.continuity_url)
        refresh_request = DemoRefreshRequest.objects.get()
        self.assertEqual(refresh_request.requested_by, self.superadmin)
        self.assertEqual(refresh_request.status, DemoRefreshRequest.Status.PENDING)
        self.assertEqual(refresh_request.base_date, timezone.localdate())
        self.assertEqual(len(refresh_request.origin_digest), 64)
        serialized = str(
            list(DemoRefreshRequest.objects.values())
            + list(SecurityThrottle.objects.values())
        )
        self.assertNotIn(self.password, serialized)
        self.assertNotIn("REGENERAR DEMO", serialized)
        self.assertNotIn("203.0.113.44", serialized)

    def test_wrong_password_phrase_or_checkbox_never_creates_a_request(self):
        self.client.force_login(self.superadmin)
        cases = (
            ({**self._valid_payload(), "current_password": "incorrecta"}, "no es correcta"),
            ({**self._valid_payload(), "confirmation_phrase": "regenerar demo"}, "exactamente"),
            ({**self._valid_payload(), "confirmation_phrase": "REGENERAR DEMO "}, "exactamente"),
            (
                {
                    "current_password": self.password,
                    "confirmation_phrase": "REGENERAR DEMO",
                },
                "obligatorio",
            ),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                response = self.client.post(self.url, payload)
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, expected, status_code=400)
                self.assertContains(response, "data-error-summary", status_code=400)
                self.assertFalse(DemoRefreshRequest.objects.exists())

    def test_second_active_request_is_rejected_and_form_is_hidden(self):
        self.client.force_login(self.superadmin)
        first = self.client.post(self.url, self._valid_payload())
        self.assertEqual(first.status_code, 302)

        review = self.client.get(self.url)
        self.assertContains(review, "La solicitud ya está registrada")
        self.assertNotContains(review, "name=\"current_password\"")

        second = self.client.post(self.url, self._valid_payload())
        self.assertEqual(second.status_code, 409)
        self.assertEqual(DemoRefreshRequest.objects.count(), 1)

    def test_attempt_limit_returns_429_before_another_password_check(self):
        self.client.force_login(self.superadmin)
        payload = {**self._valid_payload(), "current_password": "incorrecta"}
        for _index in range(5):
            self.assertEqual(self.client.post(self.url, payload).status_code, 400)

        blocked = self.client.post(self.url, payload)

        self.assertEqual(blocked.status_code, 429)
        self.assertContains(blocked, "Demasiados intentos", status_code=429)
        self.assertFalse(DemoRefreshRequest.objects.exists())

    def test_csrf_is_required_for_the_destructive_post(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.superadmin)

        response = csrf_client.post(self.url, self._valid_payload())

        self.assertEqual(response.status_code, 403)
        self.assertFalse(DemoRefreshRequest.objects.exists())

    def test_multiple_active_superadmins_fail_closed(self):
        get_user_model().objects.create_superuser(
            normalized_phone="+34910000888",
            password=self.password,
            full_name="Segundo admin",
        )
        self.client.force_login(self.superadmin)

        response = self.client.post(self.url, self._valid_payload())

        self.assertEqual(response.status_code, 404)
        self.assertFalse(DemoRefreshRequest.objects.exists())

    def test_snapshot_uses_the_latest_receipt_for_configurable_freshness(self):
        now = timezone.now()
        DemoRefreshReceipt.objects.create(
            run_id="refresh-20260701-aaaaaaaaaaaaaaaa",
            base_date=date(2026, 7, 1),
            fingerprint="a" * 64,
            completed_at=now - timedelta(days=20),
        )
        latest = DemoRefreshReceipt.objects.create(
            run_id="refresh-20260718-bbbbbbbbbbbbbbbb",
            base_date=date(2026, 7, 18),
            fingerprint="b" * 64,
            completed_at=now - timedelta(days=2),
        )

        with override_settings(AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS=7):
            snapshot = demo_refresh_snapshot(now=now)

        self.assertEqual(snapshot["latest_receipt"], latest)
        self.assertFalse(snapshot["needs_attention"])

    def test_failed_manual_receipt_never_hides_the_failure_or_refreshes_freshness(self):
        now = timezone.now()
        accepted = DemoRefreshReceipt.objects.create(
            run_id="refresh-20260716-accepted000000",
            base_date=date(2026, 7, 16),
            fingerprint="a" * 64,
            completed_at=now - timedelta(days=2),
        )
        public_id = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        failed_receipt = DemoRefreshReceipt.objects.create(
            run_id=str(public_id),
            base_date=date(2026, 7, 18),
            fingerprint="b" * 64,
            completed_at=now,
        )
        DemoRefreshRequest.objects.create(
            public_id=public_id,
            requested_by=self.superadmin,
            base_date=date(2026, 7, 18),
            status=DemoRefreshRequest.Status.FAILED,
            started_at=now - timedelta(minutes=2),
            finished_at=now,
            receipt=failed_receipt,
            failure_code="runtime_recovery_required",
            origin_digest="c" * 64,
        )

        with override_settings(AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS=7):
            snapshot = demo_refresh_snapshot(now=now)

        self.assertEqual(snapshot["latest_receipt"], accepted)
        self.assertTrue(snapshot["needs_attention"])

    def test_snapshot_flags_missing_receipt_and_noncanonical_business(self):
        Business.objects.create(commercial_name="Negocio temporal", slug="temporal")

        snapshot = demo_refresh_snapshot()

        self.assertTrue(snapshot["needs_attention"])
        self.assertTrue(snapshot["has_mutable_changes"])
        self.assertEqual(snapshot["counts"]["additional_businesses"], 1)

    def test_continuity_shows_the_completed_request_and_its_verified_receipt(self):
        now = timezone.now()
        public_id = UUID("e83e0d3c-786c-4595-9f16-29c4c4578230")
        fingerprint = "c" * 64
        receipt = DemoRefreshReceipt.objects.create(
            run_id=str(public_id),
            base_date=date(2026, 7, 18),
            fingerprint=fingerprint,
            completed_at=now,
        )
        DemoRefreshRequest.objects.create(
            public_id=public_id,
            requested_by=self.superadmin,
            base_date=date(2026, 7, 18),
            status=DemoRefreshRequest.Status.COMPLETED,
            started_at=now - timedelta(minutes=2),
            finished_at=now,
            receipt=receipt,
            origin_digest="d" * 64,
        )
        self.client.force_login(self.superadmin)

        response = self.client.get(self.continuity_url)

        self.assertContains(response, "Resultado de la última solicitud")
        self.assertContains(response, str(public_id))
        self.assertContains(response, "Escenario canónico comprobado")
        self.assertContains(response, fingerprint)
        self.assertNotContains(response, "d" * 64)

    def test_continuity_shows_a_bounded_reference_for_a_failed_request(self):
        now = timezone.now()
        failure_code = "runtime_recovery_required"
        DemoRefreshRequest.objects.create(
            requested_by=self.superadmin,
            base_date=date(2026, 7, 18),
            status=DemoRefreshRequest.Status.FAILED,
            started_at=now - timedelta(minutes=2),
            finished_at=now,
            failure_code=failure_code,
            origin_digest="e" * 64,
        )
        self.client.force_login(self.superadmin)

        response = self.client.get(self.continuity_url)

        self.assertContains(response, "No pudo darse por verificado")
        self.assertContains(response, failure_code)
        self.assertContains(response, "terminó con una incidencia operativa")
        self.assertContains(response, "Revisa la incidencia antes de solicitar otra")
        self.assertNotContains(response, "e" * 64)

    def test_continuity_distinguishes_verified_data_from_a_runtime_failure(self):
        now = timezone.now()
        public_id = UUID("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
        fingerprint = "f" * 64
        receipt = DemoRefreshReceipt.objects.create(
            run_id=str(public_id),
            base_date=date(2026, 7, 18),
            fingerprint=fingerprint,
            completed_at=now,
        )
        DemoRefreshRequest.objects.create(
            public_id=public_id,
            requested_by=self.superadmin,
            base_date=date(2026, 7, 18),
            status=DemoRefreshRequest.Status.FAILED,
            started_at=now - timedelta(minutes=2),
            finished_at=now,
            receipt=receipt,
            failure_code="runtime_recovery_required",
            origin_digest="1" * 64,
        )
        self.client.force_login(self.superadmin)

        response = self.client.get(self.continuity_url)

        self.assertContains(
            response,
            "Datos canónicos verificados; cierre operativo fallido",
        )
        self.assertContains(response, "Recibo técnico")
        self.assertContains(response, "Verificado")
        self.assertContains(response, fingerprint)
        self.assertContains(response, "runtime_recovery_required")

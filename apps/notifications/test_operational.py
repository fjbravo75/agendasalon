from django.contrib.auth import get_user_model
from django.core import mail
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.businesses.forms import BusinessVisualSettingsForm, PlatformVisualSettingsForm
from apps.businesses.models import (
    Business,
    BusinessMembership,
    PlatformActivityEvent,
    PlatformSettings,
)
from apps.core.models import SecurityThrottle
from apps.notifications.forms import (
    BusinessNotificationSettingsForm,
    PlatformNotificationSettingsForm,
)
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    dispatch_outbound_email,
    mark_operational_email_verified_from_account,
    mark_operational_email_verified_from_account_on_commit,
    operational_email_token,
    queue_operational_email_verification,
    queue_operational_notice,
    queue_operational_notice_on_commit,
)
from unittest.mock import patch


OPERATIONAL_SETTINGS = {
    "AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED": True,
    "AGENDA_TRANSACTIONAL_EMAIL_ENABLED": True,
    "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL": False,
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT": 100,
    "AGENDA_OPERATIONAL_EMAIL_DAILY_LIMIT": 500,
}


@override_settings(**OPERATIONAL_SETTINGS)
class OperationalNotificationViewTests(TestCase):
    password = "Contrasena-segura-2026"  # gitleaks:allow -- Credencial ficticia de pruebas.

    def setUp(self):
        User = get_user_model()
        self.superadmin = User.objects.create_superuser(
            normalized_phone="+34610000001",
            phone="610 000 001",
            password=self.password,
            full_name="Administración AgendaSalon",
            email="superadmin@example.com",
            email_verified_at=timezone.now(),
        )
        self.business = Business.objects.create(
            commercial_name="Salón de prueba",
            slug="salon-prueba",
        )
        self.professional = User.objects.create_user(
            normalized_phone="+34610000002",
            phone="610 000 002",
            password=self.password,
            full_name="Profesional de prueba",
            email="profesional@example.com",
            email_verified_at=timezone.now(),
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
            role=BusinessMembership.Role.PROFESSIONAL_ADMIN,
        )

    def _platform_payload(self, email):
        return {
            "notification_email": email,
            "notifications_enabled": "on",
            "notify_continuity": "on",
            "notify_demo_refresh": "on",
            "notify_signup_requests": "on",
            "notify_email_failures": "on",
        }

    def test_feature_flag_hides_navigation_and_blocks_the_route(self):
        self.client.force_login(self.superadmin)
        with override_settings(AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED=False):
            response = self.client.get(reverse("notifications:superadmin_notifications"))
            dashboard = self.client.get(reverse("dashboards:superadmin_home"))

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(dashboard, ">Avisos<")

    def test_notification_center_stays_visible_when_delivery_is_paused(self):
        self.client.force_login(self.superadmin)
        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            response = self.client.get(reverse("notifications:superadmin_notifications"))
            dashboard = self.client.get(reverse("dashboards:superadmin_home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Entrega por correo pausada")
        self.assertContains(dashboard, ">Avisos<")

    def test_notification_center_get_does_not_create_platform_settings(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("notifications:superadmin_notifications"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(PlatformSettings.objects.exists())

    def test_platform_reuses_verified_account_without_second_email_and_traces_change(self):
        self.client.force_login(self.superadmin)
        response = self.client.post(
            reverse("notifications:platform_notification_settings"),
            self._platform_payload(self.superadmin.email),
        )

        self.assertRedirects(response, reverse("notifications:superadmin_notifications"))
        platform = PlatformSettings.objects.get(pk=PlatformSettings.SINGLETON_PK)
        self.assertEqual(platform.notification_email_normalized, self.superadmin.email_normalized)
        self.assertEqual(platform.notification_email_verified_at, self.superadmin.email_verified_at)
        self.assertFalse(OutboundEmail.objects.exists())
        event = PlatformActivityEvent.objects.get()
        self.assertEqual(
            event.event_type,
            PlatformActivityEvent.EventType.NOTIFICATION_SETTINGS_UPDATED,
        )
        self.assertNotIn(self.superadmin.email, str(event.changes))

        repeated = self.client.post(
            reverse("notifications:platform_notification_settings"),
            self._platform_payload(self.superadmin.email),
            follow=True,
        )
        self.assertContains(repeated, "No había cambios que guardar")
        self.assertEqual(PlatformActivityEvent.objects.count(), 1)

    def test_distinct_platform_email_requires_post_confirmation_without_storing_token(self):
        self.client.force_login(self.superadmin)
        destination = "avisos-plataforma@example.com"
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("notifications:platform_notification_settings"),
                self._platform_payload(destination),
            )

        self.assertEqual(response.status_code, 302)
        platform = PlatformSettings.objects.get(pk=PlatformSettings.SINGLETON_PK)
        self.assertIsNone(platform.notification_email_verified_at)
        queued = OutboundEmail.objects.get()
        self.assertEqual(queued.status, OutboundEmail.Status.SENT)
        self.assertEqual(queued.payload["code"], "verification")
        self.assertNotIn(destination, str(queued.payload))
        self.assertNotIn("token", str(queued.payload).lower())
        self.assertEqual(len(mail.outbox), 1)

        token = operational_email_token(scope="platform", target=platform)
        self.assertNotIn(destination, token)
        url = reverse("notifications:platform_email_verify", args=[token])
        preview = self.client.get(url)
        platform.refresh_from_db()
        self.assertEqual(preview.status_code, 200)
        self.assertIsNone(platform.notification_email_verified_at)

        confirmation = self.client.post(url)
        platform.refresh_from_db()
        self.assertRedirects(
            confirmation,
            reverse("notifications:superadmin_notifications"),
        )
        self.assertIsNotNone(platform.notification_email_verified_at)
        replay = self.client.get(url)
        self.assertEqual(replay.status_code, 410)
        self.assertEqual(
            PlatformActivityEvent.objects.filter(
                event_type=PlatformActivityEvent.EventType.NOTIFICATION_EMAIL_VERIFIED
            ).count(),
            1,
        )

    def test_changing_address_invalidates_previous_verification_link(self):
        self.client.force_login(self.superadmin)
        self.client.post(
            reverse("notifications:platform_notification_settings"),
            self._platform_payload("primero@example.com"),
        )
        platform = PlatformSettings.objects.get(pk=PlatformSettings.SINGLETON_PK)
        old_token = operational_email_token(scope="platform", target=platform)

        self.client.post(
            reverse("notifications:platform_notification_settings"),
            self._platform_payload("segundo@example.com"),
        )

        response = self.client.get(
            reverse("notifications:platform_email_verify", args=[old_token])
        )
        self.assertEqual(response.status_code, 410)
        self.assertContains(response, "Reenviar enlace de verificación", status_code=410)

    def test_inactive_superadmin_cannot_open_operational_notifications(self):
        self.client.force_login(self.superadmin)
        type(self.superadmin).objects.filter(pk=self.superadmin.pk).update(is_active=False)

        response = self.client.get(reverse("notifications:superadmin_notifications"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/entrar/", response.url)

    def test_professional_cannot_verify_another_business_channel(self):
        other_business = Business.objects.create(
            commercial_name="Otro salón",
            slug="otro-salon",
            notification_email="otro@example.com",
            notification_email_normalized="otro@example.com",
        )
        token = operational_email_token(scope="business", target=other_business)
        self.client.force_login(self.professional)

        response = self.client.get(
            reverse("notifications:business_email_verify", args=[token])
        )

        self.assertEqual(response.status_code, 403)
        other_business.refresh_from_db()
        self.assertIsNone(other_business.notification_email_verified_at)

    def test_business_settings_use_an_independent_form_and_leave_get_read_only(self):
        self.business.notification_email = self.professional.email
        self.business.notification_email_normalized = self.professional.email_normalized
        self.business.save()
        self.client.force_login(self.professional)
        url = reverse("business_settings:professional_settings")

        page = self.client.get(url)
        self.business.refresh_from_db()

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id="business-notifications-form"')
        self.assertContains(page, 'form="business-notifications-form"')
        self.assertIsNone(self.business.notification_email_verified_at)

    def test_business_settings_verify_matching_professional_and_trace_without_address(self):
        self.client.force_login(self.professional)
        response = self.client.post(
            reverse("business_settings:professional_settings"),
            {
                "form_kind": "notifications",
                "notification_email": self.professional.email,
                "notifications_enabled": "on",
                "notify_new_appointments": "on",
                "notify_cancellations": "on",
                "notify_client_access": "on",
                "notify_holiday_reviews": "on",
                "notify_email_failures": "on",
            },
        )

        self.assertRedirects(response, reverse("business_settings:professional_settings"))
        self.business.refresh_from_db()
        self.assertEqual(
            self.business.notification_email_verified_at,
            self.professional.email_verified_at,
        )
        event = self.business.activity_events.get(
            event_type="notification_settings_updated"
        )
        self.assertNotIn(self.professional.email, str(event.changes))
        self.assertFalse(OutboundEmail.objects.exists())

    def test_business_notification_form_is_hidden_and_post_blocked_when_feature_is_off(self):
        self.client.force_login(self.professional)
        url = reverse("business_settings:professional_settings")
        with override_settings(AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED=False):
            page = self.client.get(url)
            post = self.client.post(
                url,
                {"form_kind": "notifications", "notification_email": "x@example.com"},
            )

        self.assertNotContains(page, "Avisos del negocio")
        self.assertEqual(post.status_code, 404)

    def test_inactive_professional_cannot_test_the_operational_channel(self):
        self.business.notification_email = self.professional.email
        self.business.notification_email_normalized = self.professional.email_normalized
        self.business.notification_email_verified_at = self.professional.email_verified_at
        self.business.save()
        self.client.force_login(self.professional)
        type(self.professional).objects.filter(pk=self.professional.pk).update(is_active=False)

        response = self.client.post(reverse("notifications:business_email_test"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/entrar/", response.url)

    def test_test_email_is_rate_limited_without_disabling_the_channel(self):
        self.business.notification_email = self.professional.email
        self.business.notification_email_normalized = self.professional.email_normalized
        self.business.notification_email_verified_at = self.professional.email_verified_at
        self.business.save()
        self.client.force_login(self.professional)
        url = reverse("notifications:business_email_test")

        for _attempt in range(6):
            self.client.post(url)

        self.assertEqual(
            OutboundEmail.objects.filter(
                kind=OutboundEmail.Kind.OPERATIONAL_NOTICE,
                payload__code="test",
            ).count(),
            5,
        )
        self.business.refresh_from_db()
        self.assertTrue(self.business.notifications_enabled)
        self.assertTrue(
            SecurityThrottle.objects.filter(scope="operational_test_email").exists()
        )

    def test_test_email_requires_saving_a_changed_address_first(self):
        self.business.notification_email = self.professional.email
        self.business.notification_email_normalized = self.professional.email_normalized
        self.business.notification_email_verified_at = self.professional.email_verified_at
        self.business.save()
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("notifications:business_email_test"),
            {"notification_email": "otro@example.com"},
            follow=True,
        )

        self.assertContains(response, "Guarda primero el nuevo correo")
        self.assertFalse(OutboundEmail.objects.exists())

    def test_test_email_is_queued_without_promising_delivery(self):
        self.business.notification_email = self.professional.email
        self.business.notification_email_normalized = self.professional.email_normalized
        self.business.notification_email_verified_at = self.professional.email_verified_at
        self.business.save()
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("notifications:business_email_test"),
            follow=True,
        )

        self.assertContains(response, "Correo de prueba en cola")
        queued = OutboundEmail.objects.get(payload__code="test")
        self.assertEqual(queued.status, OutboundEmail.Status.PENDING)
        self.assertEqual(len(mail.outbox), 0)

    @patch(
        "apps.notifications.services.queue_and_dispatch",
        side_effect=lambda email: email,
    )
    def test_pending_verification_is_described_as_queued(self, _dispatch):
        self.client.force_login(self.superadmin)

        response = self.client.post(
            reverse("notifications:platform_notification_settings"),
            self._platform_payload("avisos-pendientes@example.com"),
            follow=True,
        )

        self.assertContains(response, "El enlace de verificación está en cola")
        self.assertNotContains(response, "revisa esa bandeja")

    def test_notification_forms_do_not_overwrite_visual_settings(self):
        business_instance = Business.objects.get(pk=self.business.pk)
        business_form = BusinessNotificationSettingsForm(
            {
                "notification_email": "avisos-negocio@example.com",
                "notifications_enabled": "on",
                "notify_new_appointments": "on",
                "notify_cancellations": "on",
                "notify_client_access": "on",
                "notify_holiday_reviews": "on",
                "notify_email_failures": "on",
            },
            instance=business_instance,
        )
        self.assertTrue(business_form.is_valid(), business_form.errors)
        Business.objects.filter(pk=self.business.pk).update(professional_theme="dark")
        business_form.save()
        self.business.refresh_from_db()
        self.assertEqual(self.business.professional_theme, "dark")

        platform = PlatformSettings.objects.create()
        platform_form = PlatformNotificationSettingsForm(
            self._platform_payload("avisos-plataforma@example.com"),
            instance=PlatformSettings.objects.get(pk=platform.pk),
            actor=self.superadmin,
        )
        self.assertTrue(platform_form.is_valid(), platform_form.errors)
        PlatformSettings.objects.filter(pk=platform.pk).update(admin_theme="dark")
        platform_form.save()
        platform.refresh_from_db()
        self.assertEqual(platform.admin_theme, "dark")

    def test_visual_forms_do_not_overwrite_notification_settings(self):
        business_form = BusinessVisualSettingsForm(
            {
                "professional_theme": "dark",
                "public_image_choice": "preset:salon",
            },
            instance=Business.objects.get(pk=self.business.pk),
        )
        self.assertTrue(business_form.is_valid(), business_form.errors)
        Business.objects.filter(pk=self.business.pk).update(
            notification_email="conservar-negocio@example.com",
            notification_email_normalized="conservar-negocio@example.com",
        )
        business_form.save(uploaded_by=self.professional)
        self.business.refresh_from_db()
        self.assertEqual(
            self.business.notification_email_normalized,
            "conservar-negocio@example.com",
        )

        platform = PlatformSettings.objects.create()
        platform_form = PlatformVisualSettingsForm(
            {
                "admin_theme": "dark",
                "login_image_choice": "preset:agendasalon",
            },
            instance=PlatformSettings.objects.get(pk=platform.pk),
        )
        self.assertTrue(platform_form.is_valid(), platform_form.errors)
        PlatformSettings.objects.filter(pk=platform.pk).update(
            notification_email="conservar-plataforma@example.com",
            notification_email_normalized="conservar-plataforma@example.com",
        )
        platform_form.save(updated_by=self.superadmin)
        platform.refresh_from_db()
        self.assertEqual(
            platform.notification_email_normalized,
            "conservar-plataforma@example.com",
        )


@override_settings(**OPERATIONAL_SETTINGS)
class OperationalNotificationServiceTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Negocio avisado",
            slug="negocio-avisado",
            notification_email="avisos@example.com",
            notification_email_normalized="avisos@example.com",
            notification_email_verified_at=timezone.now(),
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34610000019",
            phone="610 000 019",
            password="Contrasena-segura-2026",  # gitleaks:allow -- Credencial ficticia de pruebas.
            full_name="Responsable de avisos",
            is_active=True,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
            role=BusinessMembership.Role.PROFESSIONAL_ADMIN,
        )

    def _business_notification_payload(self, email):
        return {
            "notification_email": email,
            "notifications_enabled": "on",
            "notify_new_appointments": "on",
            "notify_cancellations": "on",
            "notify_client_access": "on",
            "notify_holiday_reviews": "on",
            "notify_email_failures": "on",
        }

    def test_preferences_and_deduplication_are_enforced(self):
        self.business.notify_new_appointments = False
        self.business.save(update_fields=["notify_new_appointments", "updated_at"])
        disabled = queue_operational_notice(
            scope="business",
            code="new_appointment",
            deduplication_key="appointment:1",
            business=self.business,
        )
        self.assertIsNone(disabled)

        self.business.notify_new_appointments = True
        self.business.save(update_fields=["notify_new_appointments", "updated_at"])
        first = queue_operational_notice(
            scope="business",
            code="new_appointment",
            deduplication_key="appointment:1",
            business=self.business,
        )
        second = queue_operational_notice(
            scope="business",
            code="new_appointment",
            deduplication_key="appointment:1",
            business=self.business,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OutboundEmail.objects.count(), 1)

    def test_unknown_operational_codes_are_not_queued(self):
        email = queue_operational_notice(
            scope="business",
            code="invented_event",
            deduplication_key="invented:1",
            business=self.business,
        )

        self.assertIsNone(email)
        self.assertFalse(OutboundEmail.objects.exists())

    def test_only_current_verification_nonce_can_be_dispatched_after_a_b_a_change(self):
        first = queue_operational_email_verification(
            scope="business",
            target=self.business,
            business=self.business,
        )

        for address in ("otro@example.com", "avisos@example.com"):
            current_business = Business.objects.get(pk=self.business.pk)
            form = BusinessNotificationSettingsForm(
                self._business_notification_payload(address),
                instance=current_business,
            )
            self.assertTrue(form.is_valid(), form.errors)
            form.save()

        current_business = Business.objects.get(pk=self.business.pk)
        current = queue_operational_email_verification(
            scope="business",
            target=current_business,
            business=current_business,
        )

        obsolete_delivery = dispatch_outbound_email(first.pk)
        current_delivery = dispatch_outbound_email(current.pk)

        self.assertEqual(obsolete_delivery.status, OutboundEmail.Status.CANCELLED)
        self.assertEqual(current_delivery.status, OutboundEmail.Status.SENT)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False)
    def test_operational_notice_is_not_queued_without_real_delivery(self):
        email = queue_operational_notice(
            scope="business",
            code="new_appointment",
            deduplication_key="delivery-off:1",
            business=self.business,
        )

        self.assertIsNone(email)
        self.assertFalse(OutboundEmail.objects.exists())

    def test_inactive_business_does_not_receive_operational_notices(self):
        self.business.is_active = False
        self.business.save(update_fields=["is_active", "updated_at"])

        email = queue_operational_notice(
            scope="business",
            code="new_appointment",
            deduplication_key="inactive:1",
            business=self.business,
        )

        self.assertIsNone(email)
        self.assertFalse(OutboundEmail.objects.exists())

    def test_verified_professional_only_verifies_the_channel_of_its_business(self):
        User = get_user_model()
        verified_at = timezone.now()
        professional = User.objects.create_user(
            normalized_phone="+34610000009",
            phone="610 000 009",
            password="Contrasena-segura-2026",  # gitleaks:allow -- Credencial ficticia de pruebas.
            full_name="Profesional verificado",
            email=self.business.notification_email,
            email_verified_at=verified_at,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=professional,
            role=BusinessMembership.Role.PROFESSIONAL_ADMIN,
        )
        self.business.notification_email_verified_at = None
        self.business.save(update_fields=["notification_email_verified_at", "updated_at"])

        verified = mark_operational_email_verified_from_account(professional)

        self.business.refresh_from_db()
        self.assertEqual(verified, (("business", self.business.pk),))
        self.assertEqual(self.business.notification_email_verified_at, verified_at)
        self.assertTrue(
            self.business.activity_events.filter(
                event_type="notification_settings_updated"
            ).exists()
        )

    @patch(
        "apps.notifications.services.mark_operational_email_verified_from_account",
        side_effect=RuntimeError("fallo operativo aislado"),
    )
    def test_operational_reuse_failure_does_not_rollback_the_verified_fact(self, _reuse):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                self.business.city = "Sevilla"
                self.business.save(update_fields=["city", "updated_at"])
                mark_operational_email_verified_from_account_on_commit(self.professional)

        self.business.refresh_from_db()
        self.assertEqual(self.business.city, "Sevilla")

    @override_settings(
        AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT=1,
        AGENDA_OPERATIONAL_EMAIL_DAILY_LIMIT=2,
    )
    def test_global_capacity_stops_excess_without_exposing_identity(self):
        first = queue_operational_notice(
            scope="business",
            code="new_appointment",
            deduplication_key="capacity:1",
            business=self.business,
        )
        second = queue_operational_notice(
            scope="business",
            code="new_appointment",
            deduplication_key="capacity:2",
            business=self.business,
        )

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(OutboundEmail.objects.count(), 1)

    @patch(
        "apps.notifications.services.queue_operational_notice",
        side_effect=RuntimeError("fallo aislado"),
    )
    def test_notice_failure_does_not_rollback_the_originating_fact(self, _queue):
        with self.captureOnCommitCallbacks(execute=True):
            with transaction.atomic():
                self.business.city = "Madrid"
                self.business.save(update_fields=["city", "updated_at"])
                queue_operational_notice_on_commit(
                    scope="business",
                    code="new_appointment",
                    deduplication_key="isolated:1",
                    business=self.business,
                )

        self.business.refresh_from_db()
        self.assertEqual(self.business.city, "Madrid")

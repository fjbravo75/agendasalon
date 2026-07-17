import re
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import Business
from apps.core.models import SecurityThrottle
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessGrant,
)
from apps.customers.services import (
    PUBLIC_REGISTRATION_RETENTION_SECONDS,
    purge_expired_public_registrations,
    register_client_access,
)
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    CLIENT_EMAIL_TOKEN_MAX_AGE,
    _is_still_valid,
    client_verification_token,
    queue_client_email_verification,
    unverified_client_from_token,
    verified_client_from_token,
)


class PendingPublicRegistrationRetentionTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Salón con alta pendiente",
            slug="salon-alta-pendiente",
            is_active=True,
            public_booking_enabled=True,
        )
        self.registration_url = reverse(
            "customers:client_register",
            args=[self.business.slug],
        )

    def _register(
        self,
        *,
        email="pendiente@example.com",
        name="Nombre inicial",
        phone="600880001",
    ):
        return register_client_access(
            business=self.business,
            full_name=name,
            phone=phone,
            email=email,
        )

    def _expire(self, access, *, at=None):
        at = at or timezone.now()
        BusinessClientAccess.objects.filter(pk=access.pk).update(
            public_registration_expires_at=at
        )
        access.refresh_from_db()
        return access

    def _observable_pending_html(self, response):
        return re.sub(
            r'name="csrfmiddlewaretoken" value="[^"]+"',
            'name="csrfmiddlewaretoken" value="<token>"',
            response.content.decode(),
        )

    def test_retention_matches_the_visible_verification_token_lifetime(self):
        now = timezone.now()
        with patch("apps.customers.services.timezone.now", return_value=now):
            access = self._register()

        self.assertEqual(PUBLIC_REGISTRATION_RETENTION_SECONDS, 48 * 60 * 60)
        self.assertEqual(
            PUBLIC_REGISTRATION_RETENTION_SECONDS,
            CLIENT_EMAIL_TOKEN_MAX_AGE,
        )
        self.assertEqual(
            access.public_registration_expires_at,
            now + timedelta(seconds=CLIENT_EMAIL_TOKEN_MAX_AGE),
        )

    def test_pending_page_is_observably_equal_for_new_pending_and_existing_email(self):
        owner_browser = Client()
        owner_response = owner_browser.post(
            self.registration_url,
            {
                "full_name": "Nombre privado",
                "phone": "600880020",
                "email": "privado@example.com",
            },
        )
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="privado@example.com",
        )
        original_expiry = access.public_registration_expires_at
        new_page = owner_browser.get(owner_response["Location"])

        pending_browser = Client()
        with patch("apps.customers.views.queue_client_email_verification") as queue:
            pending_response = pending_browser.post(
                self.registration_url,
                {
                    "full_name": "Intento ajeno",
                    "phone": "600880021",
                    "email": "privado@example.com",
                },
            )

        self.assertEqual(pending_response.status_code, 302)
        self.assertEqual(pending_response["Location"], owner_response["Location"])
        queue.assert_not_called()
        self.assertIsNone(
            pending_browser.session["client_email_verification_pending"]["access_id"]
        )
        pending_page = pending_browser.get(pending_response["Location"])

        access.refresh_from_db()
        access.business_client.refresh_from_db()
        self.assertEqual(access.business_client.full_name, "Nombre privado")
        self.assertEqual(access.business_client.phone, "600880020")
        self.assertEqual(access.phone, "600880020")
        self.assertEqual(access.public_registration_expires_at, original_expiry)

        verified_client_from_token(
            client_verification_token(access),
            business=self.business,
            # gitleaks:allow -- Credencial ficticia limitada a esta prueba aislada.
            password="Cuenta-ya-existente-2026",  # gitleaks:allow
        )
        existing_browser = Client()
        existing_response = existing_browser.post(
            self.registration_url,
            {
                "full_name": "Otro intento ajeno",
                "phone": "600880022",
                "email": "privado@example.com",
            },
        )
        existing_page = existing_browser.get(existing_response["Location"])

        observable = self._observable_pending_html(new_page)
        self.assertEqual(observable, self._observable_pending_html(pending_page))
        self.assertEqual(observable, self._observable_pending_html(existing_page))
        self.assertContains(new_page, "Solicitar otro enlace")
        self.assertContains(new_page, "Volver al registro")
        self.assertNotContains(new_page, "Corregir nombre o teléfono")
        owner_pending = owner_browser.session["client_email_verification_pending"]
        self.assertNotIn("full_name", owner_pending)
        self.assertNotIn("phone", owner_pending)

    def test_token_owner_can_correct_name_and_phone_but_not_email(self):
        access = self._register(
            email="identidad@example.com",
            name="Identidad pendiente",
            phone="600880015",
        )
        access_id = access.pk
        client_id = access.business_client_id
        verification_url = reverse(
            "customers:client_email_verify",
            args=[self.business.slug, client_verification_token(access)],
        )

        page = self.client.get(verification_url)
        self.assertEqual(page["Cache-Control"], "no-store")
        self.assertContains(page, 'value="Identidad pendiente"')
        self.assertContains(page, 'value="600880015"')
        self.assertContains(page, "identidad@example.com")
        self.assertNotContains(page, 'name="email"')
        self.assertContains(page, "Nombre y teléfono son obligatorios")
        self.assertContains(page, 'inputmode="tel"')

        invalid = self.client.post(
            verification_url,
            {
                "full_name": "",
                "phone": "",
                "password": "Clave-segura-token-2026",
                "password_confirm": "Clave-segura-token-2026",
            },
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, "Indica tu nombre.")
        self.assertContains(invalid, "Indica tu teléfono.")

        response = self.client.post(
            verification_url,
            {
                "full_name": "Identidad corregida",
                "phone": "600880016",
                "email": "correo-inyectado@example.com",
                "password": "Clave-segura-token-2026",
                "password_confirm": "Clave-segura-token-2026",
            },
        )

        self.assertEqual(response.status_code, 302)
        access = BusinessClientAccess.objects.select_related("business_client").get(
            pk=access_id
        )
        self.assertEqual(access.business_client_id, client_id)
        self.assertEqual(access.business_client.full_name, "Identidad corregida")
        self.assertEqual(access.business_client.phone, "600880016")
        self.assertEqual(access.phone, "600880016")
        self.assertEqual(access.email_normalized, "identidad@example.com")
        self.assertFalse(access.is_pending_public_registration)
        self.assertIsNone(access.public_registration_expires_at)
        self.assertIsNotNone(access.email_verified_at)
        self.assertTrue(access.business_client.is_active)
        self.assertEqual(
            BusinessClientAccess.objects.filter(business=self.business).count(),
            1,
        )

    def test_expired_token_and_outbox_are_rejected(self):
        access = self._register(email="caducada@example.com")
        token = client_verification_token(access)
        email = queue_client_email_verification(access)
        self._expire(access, at=timezone.now() - timedelta(seconds=1))

        self.assertIsNone(unverified_client_from_token(token, business=self.business))
        email.refresh_from_db()
        self.assertFalse(_is_still_valid(email))

    def test_expired_token_offers_paths_that_do_not_depend_on_pending_session(self):
        access = self._register(email="caducada-sin-sesion@example.com")
        token = client_verification_token(access)
        self._expire(access, at=timezone.now() - timedelta(seconds=1))
        browser = Client()
        verification_url = reverse(
            "customers:client_email_verify",
            args=[self.business.slug, token],
        )
        registration_url = reverse(
            "customers:client_register",
            args=[self.business.slug],
        )
        access_url = reverse(
            "customers:client_access",
            args=[self.business.slug],
        )

        response = browser.get(verification_url)

        self.assertEqual(response.status_code, 410)
        self.assertContains(
            response,
            f'href="{registration_url}">Volver a registrarme</a>',
            status_code=410,
        )
        self.assertContains(
            response,
            f'href="{access_url}">Entrar o recuperar mi contraseña</a>',
            status_code=410,
        )
        self.assertEqual(dict(browser.session), {})

    def test_pending_page_stays_generic_after_timer_purges_the_identity(self):
        response = self.client.post(
            self.registration_url,
            {
                "full_name": "Alta que caduca",
                "phone": "600880030",
                "email": "reinicio@example.com",
            },
        )
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="reinicio@example.com",
        )
        self._expire(access, at=timezone.now() - timedelta(seconds=1))
        result = purge_expired_public_registrations(business_id=self.business.pk)

        self.assertEqual(result.purged, 1)
        pending_page = self.client.get(response["Location"])
        self.assertContains(pending_page, "Un último paso")
        self.assertContains(pending_page, "Solicitar otro enlace")
        self.assertContains(pending_page, "Volver al registro")
        self.assertNotContains(pending_page, "Este registro ya no está disponible")
        self.assertNotContains(pending_page, "Corregir nombre o teléfono")

    def test_paused_business_pending_cta_points_to_available_client_access(self):
        response = self.client.post(
            self.registration_url,
            {
                "full_name": "Alta antes de la pausa",
                "phone": "600880032",
                "email": "pausa-cta@example.com",
            },
        )
        self.business.public_booking_enabled = False
        self.business.save(update_fields=["public_booking_enabled", "updated_at"])

        pending_page = self.client.get(response["Location"])
        client_access_url = reverse(
            "customers:client_access",
            args=[self.business.slug],
        )

        self.assertContains(pending_page, "Las altas online están pausadas")
        self.assertContains(
            pending_page,
            f'href="{client_access_url}">Ir al acceso de clientes</a>',
        )
        self.assertNotContains(pending_page, "Volver al registro")

    def test_active_processing_email_does_not_renew_retention(self):
        response = self.client.post(
            self.registration_url,
            {
                "full_name": "Alta en envío",
                "phone": "600880031",
                "email": "envio-activo@example.com",
            },
        )
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="envio-activo@example.com",
        )
        email = access.outbound_emails.get(
            kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION
        )
        fixed_expiry = timezone.now() + timedelta(hours=1)
        BusinessClientAccess.objects.filter(pk=access.pk).update(
            public_registration_expires_at=fixed_expiry
        )
        OutboundEmail.objects.filter(pk=email.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            lease_token=uuid4(),
            lease_expires_at=timezone.now() + timedelta(minutes=5),
        )
        SecurityThrottle.objects.filter(scope="client_email_resend_cooldown").delete()

        resend = self.client.post(response["Location"])

        self.assertEqual(resend.status_code, 302)
        access.refresh_from_db()
        self.assertEqual(access.public_registration_expires_at, fixed_expiry)

    def test_verification_clears_pending_expiry(self):
        access = self._register(email="verificada@example.com")
        token = client_verification_token(access)

        verified = verified_client_from_token(
            token,
            business=self.business,
            password="ClienteVerificadaP2-2026!",
        )

        self.assertIsNotNone(verified)
        access.refresh_from_db()
        access.business_client.refresh_from_db()
        self.assertFalse(access.is_pending_public_registration)
        self.assertIsNone(access.public_registration_expires_at)
        self.assertIsNotNone(access.email_verified_at)
        self.assertTrue(access.business_client.is_active)

    def test_purge_at_exact_expiry_removes_only_its_own_graph(self):
        now = timezone.now()
        access = self._register(email="purgable@example.com")
        client_id = access.business_client_id
        grant_id = access.booking_grants.get().pk
        email_id = queue_client_email_verification(access).pk
        self._expire(access, at=now)

        result = purge_expired_public_registrations(
            business_id=self.business.pk,
            now=now,
        )

        self.assertEqual(result.candidates, 1)
        self.assertEqual(result.eligible, 1)
        self.assertEqual(result.purged, 1)
        self.assertEqual(result.skipped, 0)
        self.assertFalse(BusinessClient.objects.filter(pk=client_id).exists())
        self.assertFalse(BusinessClientAccess.objects.filter(pk=access.pk).exists())
        self.assertFalse(BusinessClientAccessGrant.objects.filter(pk=grant_id).exists())
        self.assertFalse(OutboundEmail.objects.filter(pk=email_id).exists())

    def test_purge_includes_inactive_access_but_never_professional_source(self):
        now = timezone.now()
        inactive_access = self._register(email="inactiva@example.com")
        BusinessClientAccess.objects.filter(pk=inactive_access.pk).update(
            is_active=False,
            public_registration_expires_at=now,
        )
        professional_access = self._register(email="profesional@example.com")
        BusinessClient.objects.filter(pk=professional_access.business_client_id).update(
            source=BusinessClient.Source.PROFESSIONAL
        )
        self._expire(professional_access, at=now)

        result = purge_expired_public_registrations(
            business_id=self.business.pk,
            now=now,
        )

        self.assertEqual(result.purged, 1)
        self.assertFalse(
            BusinessClientAccess.objects.filter(pk=inactive_access.pk).exists()
        )
        self.assertTrue(
            BusinessClientAccess.objects.filter(pk=professional_access.pk).exists()
        )

    def test_active_processing_outbox_prevents_purge(self):
        now = timezone.now()
        access = self._register(email="procesando@example.com")
        email = queue_client_email_verification(access)
        OutboundEmail.objects.filter(pk=email.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            lease_token=uuid4(),
            lease_expires_at=now + timedelta(minutes=5),
        )
        self._expire(access, at=now)

        result = purge_expired_public_registrations(
            business_id=self.business.pk,
            now=now,
        )

        self.assertEqual(result.candidates, 1)
        self.assertEqual(result.eligible, 0)
        self.assertEqual(result.purged, 0)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(BusinessClientAccess.objects.filter(pk=access.pk).exists())
        self.assertTrue(OutboundEmail.objects.filter(pk=email.pk).exists())

    def test_outbox_of_another_kind_is_preserved_without_aborting_the_batch(self):
        now = timezone.now()
        access = self._register(
            email="correo-ajeno@example.com",
            phone="600880090",
        )
        email = OutboundEmail.objects.create(
            kind=OutboundEmail.Kind.CLIENT_PASSWORD_RESET,
            business=self.business,
            client_access=access,
            recipient_email=access.email,
            deduplication_key=f"p2-other-kind:{access.pk}",
        )
        self._expire(access, at=now)
        purgable = self._register(
            email="purgable-tras-correo-ajeno@example.com",
            phone="600880091",
        )
        self._expire(purgable, at=now)

        result = purge_expired_public_registrations(
            business_id=self.business.pk,
            now=now,
            batch_size=1,
        )

        self.assertEqual(result.candidates, 2)
        self.assertEqual(result.eligible, 1)
        self.assertEqual(result.purged, 1)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(BusinessClientAccess.objects.filter(pk=access.pk).exists())
        self.assertTrue(OutboundEmail.objects.filter(pk=email.pk).exists())
        self.assertFalse(
            BusinessClientAccess.objects.filter(pk=purgable.pk).exists()
        )

    def test_stale_processing_is_cancelled_then_purged_on_the_next_run(self):
        now = timezone.now()
        access = self._register(email="lease-caducada@example.com")
        email = queue_client_email_verification(access)
        OutboundEmail.objects.filter(pk=email.pk).update(
            status=OutboundEmail.Status.PROCESSING,
            lease_token=uuid4(),
            lease_expires_at=now - timedelta(seconds=1),
        )
        self._expire(access, at=now)

        dry_result = purge_expired_public_registrations(
            business_id=self.business.pk,
            now=now,
            dry_run=True,
        )
        self.assertEqual(dry_result.skipped, 1)
        email.refresh_from_db()
        self.assertEqual(email.status, OutboundEmail.Status.PROCESSING)

        first_result = purge_expired_public_registrations(
            business_id=self.business.pk,
            now=now,
        )
        self.assertEqual(first_result.purged, 0)
        self.assertEqual(first_result.skipped, 1)
        email.refresh_from_db()
        self.assertEqual(email.status, OutboundEmail.Status.CANCELLED)
        self.assertIsNone(email.lease_token)
        self.assertIsNone(email.lease_expires_at)
        self.assertTrue(BusinessClientAccess.objects.filter(pk=access.pk).exists())

        second_result = purge_expired_public_registrations(
            business_id=self.business.pk,
            now=now,
        )
        self.assertEqual(second_result.purged, 1)
        self.assertFalse(BusinessClientAccess.objects.filter(pk=access.pk).exists())
        self.assertFalse(OutboundEmail.objects.filter(pk=email.pk).exists())

    def test_batch_limit_skips_200_protected_and_reaches_the_next_purgable(self):
        now = timezone.now()
        protected_client_ids = []
        for index in range(200):
            access = self._register(
                email=f"protegida-{index:03d}@example.com",
                name=f"Alta protegida {index:03d}",
                phone=f"610{index:06d}",
            )
            protected_client_ids.append(access.business_client_id)
            self._expire(access, at=now)
        BusinessClient.objects.filter(pk__in=protected_client_ids).update(
            last_activity_at=now
        )
        purgable = self._register(
            email="purgable-tras-200@example.com",
            name="Alta purgable tras 200",
            phone="619999999",
        )
        self._expire(purgable, at=now)

        result = purge_expired_public_registrations(
            business_id=self.business.pk,
            now=now,
            batch_size=1,
        )

        self.assertEqual(result.candidates, 201)
        self.assertEqual(result.skipped, 200)
        self.assertEqual(result.eligible, 1)
        self.assertEqual(result.purged, 1)
        self.assertFalse(
            BusinessClientAccess.objects.filter(pk=purgable.pk).exists()
        )
        self.assertEqual(
            BusinessClientAccess.objects.filter(
                business_client_id__in=protected_client_ids
            ).count(),
            200,
        )

    def test_command_is_dry_run_batched_and_idempotent(self):
        now = timezone.now()
        first = self._register(email="lote-1@example.com")
        second = self._register(email="lote-2@example.com")
        self._expire(first, at=now - timedelta(seconds=1))
        self._expire(second, at=now - timedelta(seconds=1))

        dry_output = StringIO()
        call_command(
            "purge_expired_public_registrations",
            business_slug=self.business.slug,
            batch_size=1,
            dry_run=True,
            stdout=dry_output,
        )
        self.assertIn("1 purgables", dry_output.getvalue())
        self.assertEqual(
            BusinessClientAccess.objects.filter(business=self.business).count(),
            2,
        )

        first_output = StringIO()
        call_command(
            "purge_expired_public_registrations",
            business_slug=self.business.slug,
            batch_size=1,
            stdout=first_output,
        )
        self.assertIn("1 purgadas", first_output.getvalue())
        self.assertEqual(
            BusinessClientAccess.objects.filter(business=self.business).count(),
            1,
        )

        call_command(
            "purge_expired_public_registrations",
            business_slug=self.business.slug,
            batch_size=1,
        )
        final_output = StringIO()
        call_command(
            "purge_expired_public_registrations",
            business_slug=self.business.slug,
            batch_size=1,
            stdout=final_output,
        )
        self.assertIn("0 purgadas", final_output.getvalue())
        self.assertFalse(
            BusinessClientAccess.objects.filter(business=self.business).exists()
        )

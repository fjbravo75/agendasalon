import importlib
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher, is_password_usable, make_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.core.models import SecurityThrottle
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessInvitation,
    BusinessClientAccessGrant,
    BusinessClientAuthorizedContact,
)
from apps.customers.forms import ClientEmailVerificationForm, ProfessionalClientQuickForm
from apps.customers.services import (
    CLIENT_ACCESS_LAST_SEEN_SESSION_KEY,
    CLIENT_ACCESS_PASSWORD_SESSION_KEY,
    CLIENT_ACCESS_SESSION_KEY,
    authenticate_client_access,
    create_or_reuse_professional_client,
    get_bookable_client,
    register_client_access,
    set_authorized_contact_active,
)
from apps.legal.models import (
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)
from apps.legal.presentations import LEGAL_PRESENTATION_CHANGED_MESSAGE
from apps.legal.services import (
    accept_professional_legal_documents,
    business_legal_snapshot,
    get_active_document,
)
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    client_password_reset_token,
    client_password_reset_url,
    client_verification_url,
    reset_client_password_from_token,
)


class DemoClientMigrationTests(TestCase):
    def test_existing_demo_access_receives_a_verified_technical_email(self):
        business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )
        client = BusinessClient.objects.create(
            business=business,
            full_name="Cliente demo migrada",
            phone="600555444",
        )
        access = BusinessClientAccess.objects.create(
            business=business,
            business_client=client,
            phone=client.phone,
            is_active=True,
        )
        migration = importlib.import_module(
            "apps.customers.migrations.0010_preserve_demo_client_access"
        )

        migration.preserve_demo_client_access(importlib.import_module("django.apps").apps, None)

        access.refresh_from_db()
        client.refresh_from_db()
        expected_email = f"cliente{client.pk}@agendasalon.local"
        self.assertEqual(access.email, expected_email)
        self.assertEqual(access.email_normalized, expected_email)
        self.assertIsNotNone(access.email_verified_at)
        self.assertEqual(client.email, expected_email)

    def test_legacy_client_email_is_aligned_only_from_verified_non_empty_access(self):
        business = Business.objects.create(
            commercial_name="Salón migración",
            slug="salon-migracion-email",
        )
        canonical_client = BusinessClient.objects.create(
            business=business,
            full_name="Cliente desalineada",
            phone="600555445",
            email="antiguo@example.com",
        )
        canonical_access = BusinessClientAccess(
            business=business,
            business_client=canonical_client,
            phone=canonical_client.phone,
            email="canonico@example.com",
            email_verified_at=timezone.now(),
        )
        canonical_access.set_password(None)
        canonical_access.save()
        empty_client = BusinessClient.objects.create(
            business=business,
            full_name="Cliente con correo residual",
            phone="600555446",
            email="residual@example.com",
        )
        empty_access = BusinessClientAccess(
            business=business,
            business_client=empty_client,
            phone=empty_client.phone,
            email="",
            email_verified_at=timezone.now(),
        )
        empty_access.set_password(None)
        empty_access.save()
        BusinessClientAccess.objects.filter(pk=empty_access.pk).update(email="   ")
        unverified_client = BusinessClient.objects.create(
            business=business,
            full_name="Cliente pendiente de verificar",
            phone="600555447",
            email="contacto@example.com",
        )
        unverified_access = BusinessClientAccess(
            business=business,
            business_client=unverified_client,
            phone=unverified_client.phone,
            email="pendiente@example.com",
        )
        unverified_access.set_password(None)
        unverified_access.save()
        migration = importlib.import_module(
            "apps.customers.migrations.0014_sync_business_client_email_from_access"
        )

        migration.sync_business_client_email_from_access(
            importlib.import_module("django.apps").apps,
            SimpleNamespace(connection=connection),
        )
        migration.sync_business_client_email_from_access(
            importlib.import_module("django.apps").apps,
            SimpleNamespace(connection=connection),
        )

        canonical_client.refresh_from_db()
        empty_client.refresh_from_db()
        unverified_client.refresh_from_db()
        self.assertEqual(canonical_client.email, canonical_access.email)
        self.assertEqual(empty_client.email, "residual@example.com")
        self.assertEqual(unverified_client.email, "contacto@example.com")


class CustomerModelTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )

    def test_client_normalizes_name_and_phone(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="  Maria   Lopez  ",
            phone="600 111 222",
        )

        self.assertEqual(client.full_name_normalized, "maria lopez")
        self.assertEqual(client.phone_normalized, "+34600111222")

    def test_active_client_identity_is_unique_inside_business(self):
        BusinessClient.objects.create(
            business=self.business,
            full_name="María López",
            phone="600111222",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            BusinessClient.objects.create(
                business=self.business,
                full_name="Maria   Lopez",
                phone="+34 600 111 222",
            )

    @patch("apps.customers.services.check_password")
    def test_unknown_client_login_executes_a_dummy_password_check(self, check_password_mock):
        check_password_mock.return_value = False

        access = authenticate_client_access(
            business=self.business,
            phone="600999888",
            password="incorrecta",
        )

        self.assertIsNone(access)
        check_password_mock.assert_called_once()
        self.assertTrue(check_password_mock.call_args.args[1].startswith("argon2$"))

    def test_authorized_contact_must_belong_to_same_business(self):
        other_business = Business.objects.create(
            commercial_name="Salon Norte",
            slug="salon-norte",
        )
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="Lucía Gómez",
            phone="600111333",
        )

        contact = BusinessClientAuthorizedContact(
            business=other_business,
            business_client=client,
            full_name="Ana Gómez",
            phone="600111444",
        )

        with self.assertRaises(ValidationError):
            contact.full_clean()

    def test_only_one_active_primary_contact_per_client(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="Lucía Gómez",
            phone="600111333",
        )
        BusinessClientAuthorizedContact.objects.create(
            business=self.business,
            business_client=client,
            full_name="Ana Gómez",
            phone="600111444",
            is_primary_contact=True,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            BusinessClientAuthorizedContact.objects.create(
                business=self.business,
                business_client=client,
                full_name="Carlos Gomez",
                phone="600111555",
                is_primary_contact=True,
            )

    def test_public_registration_with_existing_phone_creates_an_independent_file(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="María López",
            phone="600111222",
        )

        access = register_client_access(
            business=self.business,
            full_name="Otra persona",
            phone="600111222",
            email="otra@example.test",
            password="ClienteDemo2026!",
        )

        client.refresh_from_db()
        self.assertEqual(client.full_name, "María López")
        self.assertNotEqual(access.business_client_id, client.pk)
        self.assertEqual(access.phone_normalized, client.phone_normalized)
        self.assertEqual(access.business_client.full_name, "Otra persona")

    def test_client_access_phone_can_repeat_inside_business(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="María López",
            phone="600111222",
        )
        access = BusinessClientAccess(
            business=self.business,
            business_client=client,
            phone="600111222",
        )
        access.set_password("ClienteDemo2026!")
        access.save()

        other_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Maria L.",
            phone="+34 600 111 222",
        )
        duplicate = BusinessClientAccess(
            business=self.business,
            business_client=other_client,
            phone="+34 600 111 222",
        )
        duplicate.set_password("ClienteDemo2026!")

        duplicate.save()

        self.assertEqual(
            BusinessClientAccess.objects.filter(
                business=self.business,
                phone_normalized="+34600111222",
            ).count(),
            2,
        )

    def test_client_access_email_remains_unique_inside_business(self):
        first_client = BusinessClient.objects.create(
            business=self.business,
            full_name="María López",
            phone="600111222",
        )
        first = BusinessClientAccess(
            business=self.business,
            business_client=first_client,
            phone=first_client.phone,
            email="cliente@example.test",
        )
        first.set_password("ClienteDemo2026!")
        first.save()
        second_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Otra persona",
            phone="600111223",
        )
        duplicate = BusinessClientAccess(
            business=self.business,
            business_client=second_client,
            phone=second_client.phone,
            email="CLIENTE@example.test",
        )
        duplicate.set_password("ClienteDemo2026!")

        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()

    def test_same_phone_in_another_business_does_not_block_registration(self):
        other_business = Business.objects.create(
            commercial_name="Barbería Norte",
            slug="barberia-norte",
        )
        BusinessClient.objects.create(
            business=other_business,
            full_name="Cliente Norte",
            phone="600111222",
        )

        access = register_client_access(
            business=self.business,
            full_name="Cliente Mari",
            phone="600111222",
            email="mari@example.test",
            password="ClienteDemo2026!",
        )

        self.assertEqual(access.business, self.business)
        self.assertEqual(access.business_client.full_name, "Cliente Mari")

    def test_public_registration_rejects_duplicate_email_with_generic_error(self):
        register_client_access(
            business=self.business,
            full_name="Cliente Primera",
            phone="600111230",
            email="identidad@example.test",
            password="ClienteDemo2026!",
        )

        with self.assertRaisesMessage(
            ValidationError,
            "No podemos crear una cuenta con esos datos.",
        ):
            register_client_access(
                business=self.business,
                full_name="Cliente Segunda",
                phone="600111231",
                email="IDENTIDAD@example.test",
                password="ClienteDemo2026!",
            )

        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente Segunda",
            ).exists()
        )


class ClientAccessViewTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )

    def test_client_access_page_uses_customer_copy(self):
        response = self.client.get(reverse("customers:client_access", args=[self.business.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peluquería Mari")
        self.assertContains(response, "Zona cliente de Peluquería Mari")
        self.assertContains(response, "Reserva en")
        self.assertContains(response, "Entrar para reservar")
        self.assertContains(response, "Entrar en mi cuenta")
        self.assertContains(response, "Créala en un momento")
        self.assertContains(response, "client-auth-content")
        self.assertContains(response, "client-auth-image-space")
        self.assertContains(response, "client-auth-page--salon")
        self.assertContains(
            response,
            f'href="{reverse("public_booking", args=[self.business.slug])}"',
        )
        self.assertNotContains(response, "Acceso profesional")
        self.assertNotContains(response, 'name="full_name"')
        self.assertNotContains(response, "Crear cuenta y revisar reserva")
        self.assertNotContains(response, "Acceso privado para cuentas registradas.")
        self.assertNotContains(response, "Entrar en AgendaSalon")

    def test_client_register_page_uses_separate_registration_flow(self):
        response = self.client.get(reverse("customers:client_register", args=[self.business.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cuenta cliente de Peluquería Mari")
        self.assertContains(response, "Crea tu cuenta")
        self.assertContains(response, "en Peluquería Mari")
        self.assertContains(response, "Crear cuenta cliente")
        self.assertContains(response, "Enviar enlace de verificación")
        self.assertContains(response, "Entra para reservar")
        self.assertContains(response, "client-auth-register-page")
        self.assertContains(response, "client-auth-page--salon")
        self.assertContains(
            response,
            f'href="{reverse("public_booking", args=[self.business.slug])}"',
        )
        self.assertNotContains(response, "Acceso profesional")
        self.assertNotContains(response, "Entrar y revisar reserva")
        self.assertNotContains(response, 'name="password"')

    def test_business_uses_the_selected_barbershop_visual_theme(self):
        barberia = Business.objects.create(
            commercial_name="Barbería Norte",
            slug="barberia-norte",
            is_active=True,
            public_image_preset=Business.PublicImagePreset.BARBERSHOP,
        )

        response = self.client.get(reverse("customers:client_access", args=[barberia.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zona cliente de Barbería Norte")
        self.assertContains(response, "client-auth-page--barberia")

    def test_client_login_is_scoped_to_business_slug(self):
        other_business = Business.objects.create(
            commercial_name="Salon Norte",
            slug="salon-norte",
            is_active=True,
        )
        other_client = BusinessClient.objects.create(
            business=other_business,
            full_name="Cliente Norte",
            phone="600999222",
            email="norte@example.test",
        )
        other_access = BusinessClientAccess(
            business=other_business,
            business_client=other_client,
            phone="600999222",
            email="norte@example.test",
            email_verified_at=timezone.now(),
        )
        other_access.set_password("ClienteDemo2026!")
        other_access.save()

        response = self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {
                "next": reverse("public_booking", args=[self.business.slug]),
                "phone": "600999222",
                "password": "ClienteDemo2026!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correo, teléfono o contraseña no válidos.")

        response = self.client.post(
            reverse("customers:client_access", args=[other_business.slug]),
            {
                "next": reverse("public_booking", args=[other_business.slug]),
                "phone": "600999222",
                "password": "ClienteDemo2026!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("public_booking", args=[other_business.slug])
        )

    def test_registration_waits_for_email_verification_before_booking(self):
        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "next": reverse("public_booking", args=[self.business.slug]),
                "full_name": "Cliente Web",
                "phone": "600999001",
                "email": "cliente.web@example.com",
                "password": "ClienteDemo2026!",
                "password_confirm": "ClienteDemo2026!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("customers:client_email_pending", args=[self.business.slug]),
        )
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="Cliente Web",
            phone_normalized="+34600999001",
        )
        self.assertIsNone(access.email_verified_at)
        self.assertFalse(is_password_usable(access.password_hash))
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, self.client.session)

    def test_new_client_password_uses_argon2(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente Argon2",
            phone="600999010",
            email="argon2@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )

        self.assertEqual(identify_hasher(access.password_hash).algorithm, "argon2")

    def test_successful_login_upgrades_a_legacy_pbkdf2_hash(self):
        client_file = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente heredado",
            phone="600999011",
            email="heredado@example.test",
        )
        access = BusinessClientAccess.objects.create(
            business=self.business,
            business_client=client_file,
            phone="600999011",
            email="heredado@example.test",
            email_verified_at=timezone.now(),
            password_hash=make_password("ClienteDemo2026!", hasher="pbkdf2_sha256"),
        )

        response = self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {"phone": "600999011", "password": "ClienteDemo2026!"},
        )

        self.assertEqual(response.status_code, 302)
        access.refresh_from_db()
        self.assertEqual(identify_hasher(access.password_hash).algorithm, "argon2")

    def test_stale_legacy_login_cannot_restore_password_after_reset(self):
        client_file = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente con cambio concurrente",
            phone="600999019",
            email="cambio.concurrente@example.test",
        )
        access = BusinessClientAccess.objects.create(
            business=self.business,
            business_client=client_file,
            phone="600999019",
            email="cambio.concurrente@example.test",
            email_verified_at=timezone.now(),
            password_hash=make_password("old", hasher="pbkdf2_sha256"),
        )
        stale_access = BusinessClientAccess.objects.get(pk=access.pk)
        token = client_password_reset_token(access)

        changed = reset_client_password_from_token(
            token,
            business=self.business,
            password="new",
        )

        self.assertIsNotNone(changed)
        self.assertFalse(stale_access.check_password("old"))
        access.refresh_from_db()
        self.assertFalse(access.check_password("old"))
        self.assertTrue(access.check_password("new"))

    def test_client_login_rotates_the_session_identifier(self):
        register_client_access(
            business=self.business,
            full_name="Cliente sesión",
            phone="600999012",
            email="sesion@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        session = self.client.session
        session["preserved_state"] = "ok"
        session.save()
        previous_session_key = session.session_key

        response = self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {"phone": "600999012", "password": "ClienteDemo2026!"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(self.client.session.session_key, previous_session_key)
        self.assertEqual(self.client.session["preserved_state"], "ok")

    def test_repeated_invalid_client_login_is_rate_limited(self):
        login_url = reverse("customers:client_access", args=[self.business.slug])

        responses = [
            self.client.post(
                login_url,
                {"phone": "600999099", "password": "ContraseñaIncorrecta2026!"},
            )
            for _ in range(5)
        ]
        with patch("apps.customers.forms.authenticate_client_access") as authenticate_mock:
            responses.append(
                self.client.post(
                    login_url,
                    {"phone": "600999099", "password": "ContraseñaIncorrecta2026!"},
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

    def test_registration_with_existing_phone_creates_a_separate_unverified_account(self):
        existing = BusinessClient.objects.create(
            business=self.business,
            full_name="María López",
            phone="600999002",
        )

        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Otra persona",
                "phone": "600999002",
                "email": "otra@example.com",
                "password": "ClienteDemo2026!",
                "password_confirm": "ClienteDemo2026!",
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:client_email_pending", args=[self.business.slug]),
        )
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="otra@example.com",
        )
        self.assertNotEqual(access.business_client_id, existing.pk)
        self.assertEqual(access.phone_normalized, existing.phone_normalized)
        self.assertIsNone(access.email_verified_at)

    def test_client_logout_requires_post_and_clears_the_business_session(self):
        register_client_access(
            business=self.business,
            full_name="Cliente Web",
            phone="600999001",
            email="web@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {
                "phone": "600999001",
                "password": "ClienteDemo2026!",
            },
        )
        logout_url = reverse("customers:client_logout", args=[self.business.slug])

        self.assertEqual(self.client.get(logout_url).status_code, 405)
        response = self.client.post(logout_url)

        self.assertRedirects(
            response,
            reverse("customers:client_access", args=[self.business.slug]),
        )
        booking_response = self.client.get(reverse("public_booking", args=[self.business.slug]))
        self.assertEqual(booking_response.status_code, 200)
        self.assertContains(
            booking_response, "No necesitas una cuenta para consultar servicios y horas"
        )
        self.assertNotContains(booking_response, "Reservas como")

    def test_client_session_expires_after_one_hour_without_activity(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente inactivo",
            phone="600999021",
            email="inactivo@example.test",
            password="ClienteDemo2026!",
            email_verified=True,
        )
        session = self.client.session
        session[CLIENT_ACCESS_SESSION_KEY] = access.id
        session[CLIENT_ACCESS_LAST_SEEN_SESSION_KEY] = (
            timezone.now() - timedelta(hours=1, seconds=1)
        ).isoformat()
        session.save()

        response = self.client.get(reverse("customers:client_access", args=[self.business.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, self.client.session)
        self.assertNotIn(CLIENT_ACCESS_LAST_SEEN_SESSION_KEY, self.client.session)


class ClientAccessSecurityP0Tests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari-seguridad",
            is_active=True,
            public_booking_enabled=True,
        )

    def _access(self, *, name, email, phone="600777001", password="ClienteDemo2026!"):
        return register_client_access(
            business=self.business,
            full_name=name,
            phone=phone,
            email=email,
            password=password,
            email_verified=True,
        )

    def _enable_current_legal_compliance(self):
        self.business.legal_compliance_enabled = True
        self.business.save(update_fields=["legal_compliance_enabled", "updated_at"])
        legal_actor = get_user_model().objects.create_user(
            normalized_phone="+34600777999",
            password="ProfesionalLegal2026!",
            full_name="Responsable legal",
        )
        accept_professional_legal_documents(
            user=legal_actor,
            business=self.business,
            profile_data={
                "legal_name": "Peluquería Mari, S.L.",
                "tax_identifier": "B12345678",
                "registered_address": "Calle Mayor, 10, Málaga",
                "privacy_email": "privacidad@example.com",
                "rights_contact_name": "Responsable de privacidad",
                "retention_criteria": ("Durante la relación y los plazos legales aplicables."),
            },
        )

    def test_pending_email_copy_distinguishes_disabled_from_enabled_delivery(self):
        registration = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Cliente con correo pendiente",
                "phone": "600777099",
                "email": "pendiente.demo@example.com",
            },
        )
        self.assertEqual(registration.status_code, 302)
        pending_url = reverse("customers:client_email_pending", args=[self.business.slug])

        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            demo_page = self.client.get(pending_url)
        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True):
            delivery_page = self.client.get(pending_url)

        self.assertContains(demo_page, "Correo no disponible.")
        self.assertContains(demo_page, "Envío de correo desactivado - Peluquería Mari")
        self.assertContains(demo_page, "No se ha enviado ningún mensaje.")
        self.assertNotContains(demo_page, "Solicitar otro enlace")
        self.assertContains(demo_page, 'class="alert alert--info" role="status"')
        self.assertNotContains(demo_page, "Si recibes el mensaje")
        self.assertContains(delivery_page, "Si recibes el mensaje")
        self.assertContains(delivery_page, "Revisa tu correo - Peluquería Mari")
        self.assertContains(delivery_page, "Solicitar otro enlace")
        self.assertNotContains(delivery_page, "Correo no disponible.")

    def test_registration_copy_distinguishes_disabled_from_enabled_delivery(self):
        self._enable_current_legal_compliance()
        registration_url = reverse(
            "customers:client_register",
            args=[self.business.slug],
        )

        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            demo_page = self.client.get(registration_url)
        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True):
            delivery_page = self.client.get(registration_url)
        with override_settings(
            AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True,
            AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=True,
        ):
            suppressed_page = self.client.get(registration_url)

        self.assertContains(demo_page, "Correo temporalmente")
        self.assertContains(demo_page, "Crear cuenta cliente en Peluquería Mari")
        self.assertContains(demo_page, "no podremos enviarte el")
        self.assertContains(demo_page, "Guardar solicitud")
        self.assertContains(demo_page, "Entra para reservar")
        self.assertNotContains(demo_page, "Te enviaremos un enlace")
        self.assertNotContains(demo_page, "Enviar enlace de verificación")
        self.assertNotContains(demo_page, "En el enlace de verificación")
        self.assertContains(delivery_page, "Crea tu cuenta")
        self.assertContains(delivery_page, "Crear cuenta cliente en Peluquería Mari")
        self.assertContains(delivery_page, "Te enviaremos un enlace")
        self.assertContains(delivery_page, "Enviar enlace de verificación")
        self.assertContains(delivery_page, "En el enlace de verificación")
        self.assertNotContains(delivery_page, "Registro de prueba")
        self.assertNotContains(delivery_page, "Esta demo no enviará el enlace")
        self.assertNotContains(delivery_page, "Registrar solicitud (sin envío)")
        self.assertContains(suppressed_page, "Guardar solicitud")
        self.assertNotContains(suppressed_page, "Enviar enlace de verificación")

    def test_reserved_registration_email_error_matches_the_delivery_mode(self):
        registration_url = reverse(
            "customers:client_register",
            args=[self.business.slug],
        )
        payload = {
            "full_name": "Cliente con dominio reservado",
            "phone": "600777097",
            "email": "cliente@example.test",
        }

        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            demo_response = self.client.post(registration_url, payload)
        payload["phone"] = "600777096"
        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True):
            delivery_response = self.client.post(registration_url, payload)

        self.assertEqual(demo_response.status_code, 200)
        self.assertContains(demo_response, "formato y dominio válidos")
        self.assertContains(demo_response, "envío de correos está desactivado")
        self.assertNotContains(demo_response, "correo real que pueda recibir mensajes")
        self.assertEqual(delivery_response.status_code, 200)
        self.assertContains(delivery_response, "correo real que pueda recibir mensajes")
        self.assertNotContains(delivery_response, "formato y dominio válidos")

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False)
    def test_pending_email_resend_does_not_claim_external_delivery_in_the_demo(self):
        self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Cliente con reenvío demo",
                "phone": "600777098",
                "email": "reenvio.demo@example.com",
            },
        )
        pending_url = reverse("customers:client_email_pending", args=[self.business.slug])

        response = self.client.post(pending_url, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La solicitud se ha registrado")
        self.assertContains(response, "no se ha enviado ningún enlace")
        self.assertNotContains(
            response,
            "Si los datos corresponden a una cuenta disponible",
        )

    def test_password_recovery_copy_distinguishes_disabled_from_enabled_delivery(self):
        request_url = reverse(
            "customers:client_password_reset_request",
            args=[self.business.slug],
        )

        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            demo_page = self.client.get(request_url)
        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True):
            delivery_page = self.client.get(request_url)

        self.assertContains(demo_page, "Correo no disponible.")
        self.assertContains(demo_page, "Registrar solicitud (sin envío)")
        self.assertNotContains(demo_page, "recibirás un enlace")
        self.assertContains(delivery_page, "recibirás un enlace")
        self.assertContains(delivery_page, "Enviar enlace de recuperación")
        self.assertNotContains(delivery_page, "Correo no disponible.")

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False)
    def test_password_recovery_post_explains_that_demo_email_is_not_delivered(self):
        request_url = reverse(
            "customers:client_password_reset_request",
            args=[self.business.slug],
        )

        response = self.client.post(request_url, {"email": "nadie@example.com"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La solicitud se ha registrado")
        self.assertContains(response, "no se ha enviado ningún enlace")
        self.assertNotContains(
            response,
            "Si los datos corresponden a una cuenta disponible",
        )

    def test_email_is_canonical_and_ambiguous_legacy_phone_fails_closed(self):
        first = self._access(name="Ana Uno", email="ana.uno@example.com")
        second = self._access(name="Ana Dos", email="ana.dos@example.com")

        self.assertIsNone(
            authenticate_client_access(
                business=self.business,
                phone="600777001",
                password="ClienteDemo2026!",
            )
        )
        self.assertEqual(
            authenticate_client_access(
                business=self.business,
                identifier="ANA.UNO@example.com",
                password="ClienteDemo2026!",
            ),
            first,
        )
        self.assertEqual(
            authenticate_client_access(
                business=self.business,
                identifier="ana.dos@example.com",
                password="ClienteDemo2026!",
            ),
            second,
        )

    def test_login_ui_prioritizes_email_and_keeps_legacy_phone_post_compatible(self):
        access = self._access(name="Cliente Única", email="unica@example.com")
        login_url = reverse("customers:client_access", args=[self.business.slug])

        page = self.client.get(login_url)
        self.assertContains(page, "CORREO ELECTRÓNICO O TELÉFONO")
        self.assertContains(page, 'name="identifier"')
        self.assertContains(page, "He olvidado mi contraseña")

        response = self.client.post(
            login_url,
            {"phone": access.phone, "password": "ClienteDemo2026!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session[CLIENT_ACCESS_SESSION_KEY], access.pk)
        self.assertIn(CLIENT_ACCESS_PASSWORD_SESSION_KEY, self.client.session)

    def test_registration_is_persistently_throttled_by_email_phone_and_ip(self):
        registration_url = reverse("customers:client_register", args=[self.business.slug])
        payload = {
            "full_name": "",
            "phone": "600777010",
            "email": "repetida@example.com",
        }

        responses = [self.client.post(registration_url, payload) for _ in range(4)]

        self.assertEqual(responses[-1].status_code, 429)
        self.assertContains(responses[-1], "Demasiados intentos", status_code=429)
        self.assertFalse(
            BusinessClientAccess.objects.filter(
                business=self.business,
                email_normalized="repetida@example.com",
            ).exists()
        )
        self.assertTrue(SecurityThrottle.objects.filter(scope="client_registration_email").exists())
        self.assertTrue(SecurityThrottle.objects.filter(scope="client_registration_phone").exists())
        self.assertTrue(SecurityThrottle.objects.filter(scope="client_registration_ip").exists())

    def test_new_and_duplicate_email_registration_share_the_same_public_response(self):
        self._access(name="Cuenta existente", email="existente@example.com")
        registration_url = reverse("customers:client_register", args=[self.business.slug])
        duplicate_browser = self.client_class()
        new_browser = self.client_class()

        duplicate = duplicate_browser.post(
            registration_url,
            {
                "full_name": "Intento duplicado",
                "phone": "600777020",
                "email": "EXISTENTE@example.com",
            },
        )
        new = new_browser.post(
            registration_url,
            {
                "full_name": "Alta nueva",
                "phone": "600777021",
                "email": "nueva@example.com",
            },
        )

        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(new.status_code, 302)
        self.assertEqual(duplicate["Location"], new["Location"])
        duplicate_pending = duplicate_browser.get(duplicate["Location"])
        new_pending = new_browser.get(new["Location"])
        for response in (duplicate_pending, new_pending):
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Un último paso")
            self.assertContains(response, "Solicitar otro enlace")
            self.assertNotContains(response, "No podemos crear una cuenta")
        self.assertEqual(
            BusinessClientAccess.objects.filter(
                business=self.business,
                email_normalized="existente@example.com",
            ).count(),
            1,
        )
        self.assertTrue(
            BusinessClientAccess.objects.filter(
                business=self.business,
                email_normalized="nueva@example.com",
            ).exists()
        )

    def test_exact_professional_identity_does_not_block_independent_public_registration(self):
        professional_file = BusinessClient.objects.create(
            business=self.business,
            full_name="Nombre Compartido",
            phone="600777022",
            source=BusinessClient.Source.PROFESSIONAL,
        )

        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Nombre Compartido",
                "phone": "600777022",
                "email": "identidad.publica@example.com",
            },
        )

        self.assertEqual(response.status_code, 302)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="identidad.publica@example.com",
        )
        self.assertNotEqual(access.business_client_id, professional_file.pk)
        self.assertEqual(access.business_client.source, BusinessClient.Source.OTHER)
        self.assertFalse(access.business_client.is_active)
        self.assertTrue(access.is_pending_public_registration)
        self.assertFalse(is_password_usable(access.password_hash))
        self.assertTrue(
            OutboundEmail.objects.filter(
                kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
                client_access=access,
            ).exists()
        )

    def test_pending_public_file_cannot_poison_professional_identity_reuse(self):
        pending_access = register_client_access(
            business=self.business,
            full_name="Nombre Compartido",
            phone="600777024",
            email="pendiente@example.com",
        )

        professional_file, created = create_or_reuse_professional_client(
            business=self.business,
            full_name="Nombre Compartido",
            phone="600777024",
        )

        self.assertTrue(created)
        self.assertNotEqual(professional_file.pk, pending_access.business_client_id)
        self.assertEqual(professional_file.source, BusinessClient.Source.PROFESSIONAL)
        self.assertTrue(professional_file.is_active)
        pending_access.business_client.refresh_from_db()
        self.assertFalse(pending_access.business_client.is_active)

    def test_registration_retry_from_new_browser_keeps_generic_response_without_access(self):
        registration_url = reverse("customers:client_register", args=[self.business.slug])
        payload = {
            "full_name": "Cliente Pendiente",
            "phone": "600777025",
            "email": "pendiente.retry@example.com",
        }
        self.client.post(registration_url, payload)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="pendiente.retry@example.com",
        )
        self.assertFalse(access.business_client.is_active)
        self.assertTrue(access.is_pending_public_registration)
        SecurityThrottle.objects.filter(scope="client_email_resend_cooldown").delete()

        retry_browser = self.client_class()
        with patch("apps.customers.views.queue_client_email_verification") as queue_mock:
            response = retry_browser.post(registration_url, payload)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("customers:client_email_pending", args=[self.business.slug]),
        )
        queue_mock.assert_not_called()
        self.assertIsNone(
            retry_browser.session["client_email_verification_pending"]["access_id"]
        )
        pending_page = retry_browser.get(response["Location"])
        self.assertContains(pending_page, "pendiente.retry@example.com")
        self.assertEqual(
            BusinessClientAccess.objects.filter(
                business=self.business,
                email_normalized="pendiente.retry@example.com",
            ).count(),
            1,
        )

    def test_legal_evidence_is_created_only_after_identity_verification(self):
        self._enable_current_legal_compliance()
        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Cliente Legal",
                "phone": "600777026",
                "email": "cliente.legal@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="cliente.legal@example.com",
        )
        verify_path = urlparse(client_verification_url(access)).path
        self.assertFalse(LegalAcceptance.objects.filter(client_access=access).exists())
        self.assertFalse(CustomerPrivacyEvidence.objects.filter(client_access=access).exists())

        page = self.client.get(verify_path)
        privacy_document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'name="privacy_acknowledged"')
        self.assertContains(page, f"versión {privacy_document.version}")
        self.assertContains(page, "no autoriza publicidad")
        self.assertFalse(LegalAcceptance.objects.filter(client_access=access).exists())

        rejected = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
                "legal_presentation_token": page.context["legal_presentation_token"],
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, "Confirma que has recibido la información")
        access.refresh_from_db()
        access.business_client.refresh_from_db()
        self.assertIsNone(access.email_verified_at)
        self.assertFalse(is_password_usable(access.password_hash))
        self.assertTrue(access.is_pending_public_registration)
        self.assertFalse(access.business_client.is_active)
        self.assertFalse(LegalAcceptance.objects.filter(client_access=access).exists())

        ordinary_error = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "OtraClaveCliente2026!",
                "privacy_acknowledged": "on",
                "legal_presentation_token": page.context["legal_presentation_token"],
            },
        )
        self.assertEqual(ordinary_error.status_code, 200)
        self.assertEqual(ordinary_error["Cache-Control"], "no-store")
        self.assertEqual(ordinary_error["Referrer-Policy"], "strict-origin")
        self.assertTrue(
            ordinary_error.context["verification_form"][
                "privacy_acknowledged"
            ].value()
        )
        self.assertEqual(
            ordinary_error.context["legal_presentation_token"],
            page.context["legal_presentation_token"],
        )
        access.refresh_from_db()
        self.assertIsNone(access.email_verified_at)

        confirmed = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
                "privacy_acknowledged": "on",
                "legal_presentation_token": ordinary_error.context[
                    "legal_presentation_token"
                ],
            },
        )
        self.assertEqual(confirmed.status_code, 302)
        access.refresh_from_db()
        access.business_client.refresh_from_db()
        self.assertIsNotNone(access.email_verified_at)
        self.assertTrue(access.check_password("NuevaClaveCliente2026!"))
        self.assertFalse(access.is_pending_public_registration)
        self.assertTrue(access.business_client.is_active)

        acceptance = LegalAcceptance.objects.get(client_access=access)
        evidence = CustomerPrivacyEvidence.objects.get(client_access=access)
        self.assertEqual(acceptance.context, LegalAcceptance.Context.CLIENT_REGISTRATION)
        self.assertEqual(acceptance.document, privacy_document)
        self.assertEqual(acceptance.document_hash_snapshot, privacy_document.content_hash)
        expected_legal_context = business_legal_snapshot(self.business)
        self.assertEqual(acceptance.legal_context_snapshot, expected_legal_context)
        self.assertEqual(
            evidence.channel,
            CustomerPrivacyEvidence.Channel.ONLINE_REGISTRATION,
        )
        self.assertEqual(evidence.document, privacy_document)
        self.assertEqual(evidence.legal_context_snapshot, expected_legal_context)

    def test_email_verification_stops_cleanly_without_current_privacy_document(self):
        self._enable_current_legal_compliance()
        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Cliente sin política vigente",
                "phone": "600777038",
                "email": "cliente.sin.politica@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="cliente.sin.politica@example.com",
        )
        verify_path = urlparse(client_verification_url(access)).path
        initial_page = self.client.get(verify_path)
        token = initial_page.context["legal_presentation_token"]
        LegalDocument.objects.filter(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        ).update(is_active=False)

        unavailable = self.client.get(verify_path)

        self.assertEqual(unavailable.status_code, 503)
        self.assertContains(
            unavailable,
            "Tu enlace sigue siendo válido, pero antes necesitamos poder mostrarte",
            status_code=503,
        )
        self.assertContains(
            unavailable,
            "sigue pendiente de confirmación",
            status_code=503,
        )
        self.assertNotContains(unavailable, 'name="password"', status_code=503)
        self.assertNotContains(unavailable, 'type="submit"', status_code=503)

        rejected = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
                "privacy_acknowledged": "on",
                "legal_presentation_token": token,
            },
        )

        self.assertEqual(rejected.status_code, 503)
        access.refresh_from_db()
        access.business_client.refresh_from_db()
        self.assertIsNone(access.email_verified_at)
        self.assertFalse(is_password_usable(access.password_hash))
        self.assertTrue(access.is_pending_public_registration)
        self.assertFalse(access.business_client.is_active)
        self.assertFalse(LegalAcceptance.objects.filter(client_access=access).exists())
        self.assertFalse(
            LegalAcceptanceEvent.objects.filter(client_access=access).exists()
        )
        self.assertFalse(
            CustomerPrivacyEvidence.objects.filter(client_access=access).exists()
        )
        self.assertFalse(
            CustomerPrivacyEvidenceEvent.objects.filter(client_access=access).exists()
        )

    def test_email_verification_rerenders_fresh_compliance_after_false_to_true_flip(self):
        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Cliente durante activación legal",
                "phone": "600777037",
                "email": "cliente.activacion.legal@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="cliente.activacion.legal@example.com",
        )
        verify_path = urlparse(client_verification_url(access)).path
        page = self.client.get(verify_path)
        self.assertEqual(page.context["legal_presentation_token"], "")
        original_is_valid = ClientEmailVerificationForm.is_valid

        def validate_then_enable_compliance(form):
            is_valid = original_is_valid(form)
            Business.objects.filter(pk=self.business.pk).update(
                legal_compliance_enabled=True
            )
            return is_valid

        with patch.object(
            ClientEmailVerificationForm,
            "is_valid",
            new=validate_then_enable_compliance,
        ):
            rejected = self.client.post(
                verify_path,
                {
                    "password": "NuevaClaveCliente2026!",
                    "password_confirm": "NuevaClaveCliente2026!",
                    "legal_presentation_token": "",
                },
            )

        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, LEGAL_PRESENTATION_CHANGED_MESSAGE)
        self.assertTrue(rejected.context["business"].legal_compliance_enabled)
        self.assertIsNotNone(rejected.context["privacy_document"])
        self.assertTrue(rejected.context["legal_presentation_token"])
        access.refresh_from_db()
        self.assertIsNone(access.email_verified_at)

    def test_email_verification_rejects_a_rotated_privacy_document_without_mutations(self):
        self._enable_current_legal_compliance()
        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Cliente con documento rotado",
                "phone": "600777036",
                "email": "cliente.rotacion@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="cliente.rotacion@example.com",
        )
        verify_path = urlparse(client_verification_url(access)).path
        page = self.client.get(verify_path)
        self.assertEqual(page.status_code, 200)
        old_document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)

        LegalDocument.objects.filter(pk=old_document.pk).update(is_active=False)
        replacement = LegalDocument.objects.create(
            kind=old_document.kind,
            slug="privacidad-clientes-prueba-rotacion-b",
            version="test-rotation-b",
            title=old_document.title,
            lead=old_document.lead,
            sections=old_document.sections,
            is_active=True,
        )

        rejected = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "OtraClaveCliente2026!",
                "privacy_acknowledged": "on",
                "legal_presentation_token": page.context["legal_presentation_token"],
            },
        )

        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, LEGAL_PRESENTATION_CHANGED_MESSAGE)
        self.assertContains(rejected, f"versión {replacement.version}")
        self.assertContains(rejected, 'tabindex="-1"')
        self.assertContains(rejected, 'data-error-summary')
        self.assertFalse(
            rejected.context["verification_form"]["privacy_acknowledged"].value()
        )
        self.assertNotEqual(
            rejected.context["legal_presentation_token"],
            page.context["legal_presentation_token"],
        )
        access.refresh_from_db()
        access.business_client.refresh_from_db()
        self.assertIsNone(access.email_verified_at)
        self.assertFalse(is_password_usable(access.password_hash))
        self.assertTrue(access.is_pending_public_registration)
        self.assertFalse(access.business_client.is_active)
        self.assertFalse(LegalAcceptance.objects.filter(client_access=access).exists())
        self.assertFalse(CustomerPrivacyEvidence.objects.filter(client_access=access).exists())

        reconfirmation_required = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
                "legal_presentation_token": rejected.context[
                    "legal_presentation_token"
                ],
            },
        )
        self.assertEqual(reconfirmation_required.status_code, 200)
        self.assertContains(
            reconfirmation_required,
            "Confirma que has recibido la información",
        )
        access.refresh_from_db()
        self.assertIsNone(access.email_verified_at)

    def test_paused_business_preserves_pending_public_registration_for_later(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente en pausa",
            phone="600777027",
            email="pausa.alta@example.com",
        )
        verify_path = urlparse(client_verification_url(access)).path
        self.business.is_active = False
        self.business.save(update_fields=["is_active", "updated_at"])

        page = self.client.get(verify_path)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Las altas online están pausadas")
        rejected = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
            },
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, "No hemos activado tu cuenta")
        access.refresh_from_db()
        access.business_client.refresh_from_db()
        self.assertIsNone(access.email_verified_at)
        self.assertFalse(is_password_usable(access.password_hash))
        self.assertTrue(access.is_pending_public_registration)
        self.assertFalse(access.business_client.is_active)
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, self.client.session)

        self.business.is_active = True
        self.business.save(update_fields=["is_active", "updated_at"])
        completed = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
            },
        )
        self.assertEqual(completed.status_code, 302)
        access.refresh_from_db()
        self.assertIsNotNone(access.email_verified_at)

    def test_existing_client_can_verify_email_while_business_is_paused(self):
        client_file = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente invitada",
            phone="600777028",
            source=BusinessClient.Source.PROFESSIONAL,
        )
        access = BusinessClientAccess(
            business=self.business,
            business_client=client_file,
            phone=client_file.phone,
            email="invitada.pausa@example.com",
            is_active=True,
        )
        access.set_password(None)
        access.save()
        verify_path = urlparse(client_verification_url(access)).path
        self.business.is_active = False
        self.business.public_booking_enabled = False
        self.business.save(update_fields=["is_active", "public_booking_enabled", "updated_at"])

        self.assertEqual(self.client.get(verify_path).status_code, 200)
        response = self.client.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            reverse("legal:business_privacy", args=[self.business.slug]),
        )
        access.refresh_from_db()
        self.assertIsNotNone(access.email_verified_at)
        self.assertTrue(access.check_password("NuevaClaveCliente2026!"))

    def test_paused_business_keeps_existing_login_and_privacy_available(self):
        access = self._access(
            name="Cliente con cuenta",
            email="cuenta.pausada@example.com",
            phone="600777029",
        )
        self.business.is_active = False
        self.business.public_booking_enabled = False
        self.business.save(update_fields=["is_active", "public_booking_enabled", "updated_at"])
        login_url = reverse("customers:client_access", args=[self.business.slug])
        privacy_url = reverse("legal:business_privacy", args=[self.business.slug])

        page = self.client.get(login_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Tu cuenta sigue")
        self.assertContains(page, "ejercer tus derechos")
        self.assertContains(page, f'href="{privacy_url}"')
        self.assertNotContains(page, "Créala en un momento")
        self.assertNotContains(
            page,
            reverse("customers:client_register", args=[self.business.slug]),
        )

        response = self.client.post(
            login_url,
            {
                "identifier": access.email,
                "password": "ClienteDemo2026!",
                "next": reverse("public_booking", args=[self.business.slug]),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], privacy_url)
        self.assertEqual(self.client.session[CLIENT_ACCESS_SESSION_KEY], access.pk)
        self.assertEqual(self.client.get(login_url).status_code, 302)
        self.assertEqual(self.client.get(privacy_url).status_code, 200)

        self.assertEqual(
            self.client.get(
                reverse("customers:client_register", args=[self.business.slug])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("public_booking", args=[self.business.slug])).status_code,
            404,
        )

    def test_pending_email_can_be_revisited_and_resent_while_business_is_paused(self):
        registration = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Cliente pendiente en pausa",
                "phone": "600777030",
                "email": "pendiente.pausa@example.com",
            },
        )
        self.assertEqual(registration.status_code, 302)
        pending_url = reverse("customers:client_email_pending", args=[self.business.slug])
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="pendiente.pausa@example.com",
        )
        self.business.is_active = False
        self.business.public_booking_enabled = False
        self.business.save(update_fields=["is_active", "public_booking_enabled", "updated_at"])

        page = self.client.get(pending_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Las altas online están pausadas")
        SecurityThrottle.objects.filter(scope="client_email_resend_cooldown").delete()
        with patch("apps.customers.views.queue_client_email_verification") as queue_mock:
            response = self.client.post(pending_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], pending_url)
        queue_mock.assert_called_once_with(access)

    def test_password_recovery_works_while_paused_and_enters_privacy(self):
        access = self._access(
            name="Cliente recuperación pausada",
            email="reset.pausa@example.com",
            phone="600777031",
        )
        request_url = reverse(
            "customers:client_password_reset_request",
            args=[self.business.slug],
        )
        reset_path = urlparse(client_password_reset_url(access)).path
        privacy_url = reverse("legal:business_privacy", args=[self.business.slug])
        self.business.is_active = False
        self.business.public_booking_enabled = False
        self.business.save(update_fields=["is_active", "public_booking_enabled", "updated_at"])

        request_page = self.client.get(request_url)
        self.assertEqual(request_page.status_code, 200)
        self.assertContains(request_page, "cuenta y a la información de privacidad")
        with patch("apps.customers.views.queue_client_password_reset") as queue_mock:
            requested = self.client.post(request_url, {"email": access.email})
        self.assertEqual(requested.status_code, 200)
        queue_mock.assert_called_once_with(access)

        self.assertEqual(self.client.get(reset_path).status_code, 200)
        changed = self.client.post(
            reset_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
            },
        )
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(changed["Location"], privacy_url)
        access.refresh_from_db()
        self.assertTrue(access.check_password("NuevaClaveCliente2026!"))
        self.assertEqual(self.client.session[CLIENT_ACCESS_SESSION_KEY], access.pk)
        self.assertIn(CLIENT_ACCESS_PASSWORD_SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(privacy_url).status_code, 200)

    def test_logout_remains_available_while_business_is_paused(self):
        access = self._access(
            name="Cliente salida pausada",
            email="salida.pausa@example.com",
            phone="600777032",
        )
        login_url = reverse("customers:client_access", args=[self.business.slug])
        self.client.post(
            login_url,
            {"identifier": access.email, "password": "ClienteDemo2026!"},
        )
        self.business.is_active = False
        self.business.save(update_fields=["is_active", "updated_at"])

        response = self.client.post(reverse("customers:client_logout", args=[self.business.slug]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], login_url)
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get(login_url).status_code, 200)

    def test_verification_get_is_read_only_post_is_csrf_protected_and_replay_is_gone(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente por verificar",
            phone="600777011",
            email="verificar@example.com",
            password="ClienteDemo2026!",
        )
        verify_path = urlparse(client_verification_url(access)).path
        browser = Client(enforce_csrf_checks=True)

        page = browser.get(verify_path, secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["Referrer-Policy"], "strict-origin")
        self.assertContains(page, "Confirmar correo y crear contraseña")
        access.refresh_from_db()
        self.assertIsNone(access.email_verified_at)
        self.assertFalse(is_password_usable(access.password_hash))
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, browser.session)

        rejected = browser.post(
            verify_path,
            {
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
            },
            secure=True,
        )
        self.assertEqual(rejected.status_code, 403)
        access.refresh_from_db()
        self.assertIsNone(access.email_verified_at)

        csrf_token = browser.cookies["csrftoken"].value
        mismatch = browser.post(
            verify_path,
            {
                "csrfmiddlewaretoken": csrf_token,
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "OtraClaveCliente2026!",
            },
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        self.assertEqual(mismatch.status_code, 200)
        access.refresh_from_db()
        self.assertIsNone(access.email_verified_at)
        self.assertFalse(is_password_usable(access.password_hash))

        confirmed = browser.post(
            verify_path,
            {
                "csrfmiddlewaretoken": csrf_token,
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
            },
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        self.assertEqual(confirmed.status_code, 302)
        access.refresh_from_db()
        self.assertIsNotNone(access.email_verified_at)
        self.assertTrue(access.check_password("NuevaClaveCliente2026!"))
        self.assertEqual(browser.session[CLIENT_ACCESS_SESSION_KEY], access.pk)

        replay = self.client_class().get(verify_path)
        self.assertEqual(replay.status_code, 410)

    def test_old_verification_token_cannot_revive_after_email_b_c_b_rotation(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente con rotación",
            phone="600777023",
            email="b@example.com",
        )
        old_path = urlparse(client_verification_url(access)).path

        access.email = "c@example.com"
        access.set_password(None)
        access.save()
        access.email = "b@example.com"
        access.set_password(None)
        access.save()

        self.assertEqual(self.client.get(old_path).status_code, 410)
        fresh_path = urlparse(client_verification_url(access)).path
        self.assertEqual(self.client.get(fresh_path).status_code, 200)

    def test_verification_resend_has_cooldown_and_persistent_address_limit(self):
        registration_url = reverse("customers:client_register", args=[self.business.slug])
        response = self.client.post(
            registration_url,
            {
                "full_name": "Cliente Reenvío",
                "phone": "600777012",
                "email": "reenvio@example.com",
                "password": "ClienteDemo2026!",
                "password_confirm": "ClienteDemo2026!",
            },
        )
        self.assertEqual(response.status_code, 302)
        pending_url = reverse("customers:client_email_pending", args=[self.business.slug])

        with patch("apps.customers.views.queue_client_email_verification") as queue_mock:
            self.client.post(pending_url)
        queue_mock.assert_not_called()

        # Aislamos el límite horario del correo eliminando solo el cooldown y
        # el límite IP entre peticiones. El contador por dirección permanece.
        for _ in range(4):
            SecurityThrottle.objects.filter(
                scope__in=["client_email_resend_cooldown", "client_email_resend_ip"]
            ).delete()
            self.client.post(pending_url)
        SecurityThrottle.objects.filter(
            scope__in=["client_email_resend_cooldown", "client_email_resend_ip"]
        ).delete()
        with patch("apps.customers.views.queue_client_email_verification") as queue_mock:
            blocked = self.client.post(pending_url)
        self.assertEqual(blocked.status_code, 302)
        queue_mock.assert_not_called()
        address_throttle = SecurityThrottle.objects.get(scope="client_email_resend_address")
        self.assertEqual(address_throttle.attempts, 5)
        self.assertIsNotNone(address_throttle.blocked_until)

    def test_password_reset_request_is_generic_for_known_and_unknown_email(self):
        self._access(name="Cliente Recuperación", email="recuperacion@example.com")
        request_url = reverse(
            "customers:client_password_reset_request",
            args=[self.business.slug],
        )

        known = self.client.post(request_url, {"email": "recuperacion@example.com"})
        unknown = self.client_class().post(request_url, {"email": "nadie@example.com"})

        self.assertEqual(known.status_code, 200)
        self.assertEqual(unknown.status_code, 200)
        self.assertContains(known, "Si los datos corresponden a una cuenta disponible")
        self.assertContains(unknown, "Si los datos corresponden a una cuenta disponible")
        self.assertEqual(
            OutboundEmail.objects.filter(kind=OutboundEmail.Kind.CLIENT_PASSWORD_RESET).count(),
            1,
        )
        self.client.post(request_url, {"email": "recuperacion@example.com"})
        self.client.post(request_url, {"email": "recuperacion@example.com"})
        with patch("apps.customers.views.queue_client_password_reset") as queue_mock:
            throttled = self.client.post(
                request_url,
                {"email": "recuperacion@example.com"},
            )
        self.assertEqual(throttled.status_code, 200)
        self.assertContains(
            throttled,
            "Si los datos corresponden a una cuenta disponible",
        )
        queue_mock.assert_not_called()

    def test_password_reset_is_one_time_and_invalidates_existing_sessions(self):
        access = self._access(name="Cliente Sesión", email="sesion.segura@example.com")
        login_url = reverse("customers:client_access", args=[self.business.slug])
        old_browser = self.client_class()
        self.assertEqual(
            old_browser.post(
                login_url,
                {
                    "identifier": access.email,
                    "password": "ClienteDemo2026!",
                },
            ).status_code,
            302,
        )
        reset_path = urlparse(client_password_reset_url(access)).path
        reset_browser = Client(enforce_csrf_checks=True)

        page = reset_browser.get(reset_path, secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page["Referrer-Policy"], "strict-origin")
        access.refresh_from_db()
        self.assertTrue(access.check_password("ClienteDemo2026!"))
        csrf_token = reset_browser.cookies["csrftoken"].value

        changed = reset_browser.post(
            reset_path,
            {
                "csrfmiddlewaretoken": csrf_token,
                "password": "NuevaClaveCliente2026!",
                "password_confirm": "NuevaClaveCliente2026!",
            },
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        self.assertEqual(changed.status_code, 302)
        access.refresh_from_db()
        self.assertFalse(access.check_password("ClienteDemo2026!"))
        self.assertTrue(access.check_password("NuevaClaveCliente2026!"))
        self.assertEqual(reset_browser.get(reset_path).status_code, 410)

        old_session_response = old_browser.get(login_url)
        self.assertEqual(old_session_response.status_code, 200)
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, old_browser.session)
        self.assertNotIn(CLIENT_ACCESS_PASSWORD_SESSION_KEY, old_browser.session)
        self.assertEqual(
            old_browser.post(
                login_url,
                {
                    "identifier": access.email,
                    "password": "NuevaClaveCliente2026!",
                },
            ).status_code,
            302,
        )

    def test_password_reset_token_expires_and_is_scoped_to_business(self):
        access = self._access(name="Cliente Token", email="token@example.com")
        reset_path = urlparse(client_password_reset_url(access)).path
        other_business = Business.objects.create(
            commercial_name="Otro salón",
            slug="otro-salon-seguridad",
            is_active=True,
        )
        token = reset_path.rstrip("/").split("/")[-1]
        wrong_business_path = reverse(
            "customers:client_password_reset",
            args=[other_business.slug, token],
        )

        self.assertEqual(self.client.get(wrong_business_path).status_code, 410)
        with patch(
            "apps.notifications.services.CLIENT_PASSWORD_RESET_TOKEN_MAX_AGE",
            -1,
        ):
            self.assertEqual(self.client.get(reset_path).status_code, 410)

    def test_session_is_rejected_if_verified_email_is_withdrawn(self):
        access = self._access(name="Cliente Verificada", email="verificada@example.com")
        login_url = reverse("customers:client_access", args=[self.business.slug])
        self.client.post(
            login_url,
            {"identifier": access.email, "password": "ClienteDemo2026!"},
        )
        BusinessClientAccess.objects.filter(pk=access.pk).update(email_verified_at=None)

        response = self.client.get(login_url)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, self.client.session)
        self.assertNotIn(CLIENT_ACCESS_PASSWORD_SESSION_KEY, self.client.session)


class ClientAccessInvitationTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
            public_booking_enabled=True,
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600111991",
            password="ProfesionalDemo2026!",
            full_name="Mari Profesional",
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
        )
        self.business_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Lucía Cliente",
            phone="600111992",
        )
        self.client.force_login(self.professional)

    def _create_invitation(self):
        response = self.client.post(
            reverse(
                "customers:professional_client_invitation_create",
                args=[self.business_client.id],
            )
        )
        self.assertEqual(response.status_code, 200)
        return response, response.context["invitation"]

    def test_professional_creates_a_one_time_invitation_without_storing_the_token(self):
        response, invitation = self._create_invitation()
        invitation_url = response.context["invitation_url"]
        raw_token = invitation_url.rstrip("/").split("/")[-1]

        self.assertNotEqual(invitation.token_digest, raw_token)
        self.assertNotIn(raw_token, invitation.token_digest)
        self.assertGreater(invitation.expires_at, timezone.now())
        self.assertContains(response, "Se muestra solo en esta pantalla")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=BusinessActivityEvent.EventType.CLIENT_INVITATION_CREATED,
            ).exists()
        )

    def test_invitation_activation_copy_distinguishes_disabled_from_enabled_delivery(self):
        response, _ = self._create_invitation()
        claim_path = urlparse(response.context["invitation_url"]).path
        customer_browser = self.client_class()
        customer_browser.get(claim_path)
        activation_url = reverse(
            "customers:client_invitation_activate",
            args=[self.business.slug],
        )

        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            demo_page = customer_browser.get(activation_url)
        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True):
            delivery_page = customer_browser.get(activation_url)

        self.assertContains(demo_page, "Correo no disponible.")
        self.assertContains(demo_page, "Continuar sin envío externo")
        self.assertContains(demo_page, "el enlace no se enviará")
        self.assertNotContains(demo_page, "Enviar correo de activación")
        self.assertContains(delivery_page, "Enviar correo de activación")
        self.assertContains(delivery_page, "Lo verificaremos antes")
        self.assertNotContains(delivery_page, "Correo no disponible.")

    def test_new_invitation_revokes_the_previous_pending_one(self):
        _, first = self._create_invitation()
        _, second = self._create_invitation()

        first.refresh_from_db()
        self.assertIsNotNone(first.revoked_at)
        self.assertIsNone(second.revoked_at)

    def test_verified_invitation_activates_the_exact_client_and_cannot_be_replayed(self):
        response, invitation = self._create_invitation()
        claim_path = urlparse(response.context["invitation_url"]).path
        customer_browser = self.client_class()

        claim_response = customer_browser.get(claim_path)
        self.assertRedirects(
            claim_response,
            reverse("customers:client_invitation_activate", args=[self.business.slug]),
        )
        self.assertEqual(claim_response["Referrer-Policy"], "no-referrer")

        activation_response = customer_browser.post(
            reverse("customers:client_invitation_activate", args=[self.business.slug]),
            {
                "email": "invitada@example.com",
                "password": "ClienteInvitado2026!",
                "password_confirm": "ClienteInvitado2026!",
            },
        )
        self.assertRedirects(
            activation_response,
            reverse("customers:client_email_pending", args=[self.business.slug]),
        )

        access = BusinessClientAccess.objects.get(business_client=self.business_client)
        invitation.refresh_from_db()
        self.assertEqual(access.business, self.business)
        self.assertIsNotNone(invitation.used_at)
        self.assertFalse(is_password_usable(access.password_hash))
        self.assertIsNone(access.email_verified_at)
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, customer_browser.session)
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=BusinessActivityEvent.EventType.CLIENT_ACCESS_ACTIVATED,
                entity_id=self.business_client.id,
            ).exists()
        )

        replay_response = self.client_class().get(claim_path)
        self.assertEqual(replay_response.status_code, 410)
        self.assertContains(replay_response, "Este enlace ya", status_code=410)

    def test_invitation_privacy_evidence_is_recorded_only_after_email_verification(self):
        self.business.legal_compliance_enabled = True
        self.business.save(update_fields=["legal_compliance_enabled", "updated_at"])
        accept_professional_legal_documents(
            user=self.professional,
            business=self.business,
            profile_data={
                "legal_name": "Peluquería Mari, S.L.",
                "tax_identifier": "B12345678",
                "registered_address": "Calle Mayor, 10, Málaga",
                "privacy_email": "privacidad@example.com",
                "rights_contact_name": "Responsable de privacidad",
                "retention_criteria": ("Durante la relación y los plazos legales aplicables."),
            },
        )
        response, _ = self._create_invitation()
        claim_path = urlparse(response.context["invitation_url"]).path
        customer_browser = self.client_class()
        customer_browser.get(claim_path)
        activation = customer_browser.post(
            reverse("customers:client_invitation_activate", args=[self.business.slug]),
            {"email": "invitada.legal@example.com"},
        )
        self.assertEqual(activation.status_code, 302)
        access = BusinessClientAccess.objects.get(business_client=self.business_client)
        self.business_client.refresh_from_db()
        self.assertEqual(self.business_client.email, "")
        self.assertFalse(LegalAcceptance.objects.filter(client_access=access).exists())
        self.assertFalse(CustomerPrivacyEvidence.objects.filter(client_access=access).exists())

        verify_path = urlparse(client_verification_url(access)).path
        verification_page = customer_browser.get(verify_path)
        self.assertEqual(verification_page.status_code, 200)
        verified = customer_browser.post(
            verify_path,
            {
                "password": "ClienteInvitado2026!",
                "password_confirm": "ClienteInvitado2026!",
                "privacy_acknowledged": "on",
                "legal_presentation_token": verification_page.context[
                    "legal_presentation_token"
                ],
            },
        )

        self.assertEqual(verified.status_code, 302)
        self.business_client.refresh_from_db()
        self.assertEqual(self.business_client.email, "invitada.legal@example.com")
        acceptance = LegalAcceptance.objects.get(client_access=access)
        evidence = CustomerPrivacyEvidence.objects.get(client_access=access)
        self.assertEqual(acceptance.context, LegalAcceptance.Context.CLIENT_INVITATION)
        self.assertEqual(
            evidence.channel,
            CustomerPrivacyEvidence.Channel.CLIENT_INVITATION,
        )

    def test_activation_accepts_a_valid_same_origin_csrf_submission(self):
        response, _ = self._create_invitation()
        claim_path = urlparse(response.context["invitation_url"]).path
        csrf_browser = Client(enforce_csrf_checks=True)
        csrf_browser.get(claim_path, secure=True)
        activation_url = reverse(
            "customers:client_invitation_activate",
            args=[self.business.slug],
        )
        activation_page = csrf_browser.get(activation_url, secure=True)
        self.assertEqual(activation_page["Referrer-Policy"], "same-origin")
        csrf_token = csrf_browser.cookies["csrftoken"].value

        response = csrf_browser.post(
            activation_url,
            {
                "csrfmiddlewaretoken": csrf_token,
                "email": "csrf@example.com",
                "password": "ClienteInvitado2026!",
                "password_confirm": "ClienteInvitado2026!",
            },
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )

        self.assertRedirects(
            response,
            reverse("customers:client_email_pending", args=[self.business.slug]),
        )

    def test_invitation_cannot_be_used_from_another_business_url(self):
        response, _ = self._create_invitation()
        raw_token = response.context["invitation_url"].rstrip("/").split("/")[-1]
        invitation = BusinessClientAccessInvitation.objects.latest("created_at")
        other_business = Business.objects.create(
            commercial_name="Barbería Norte",
            slug="barberia-norte",
            is_active=True,
            public_booking_enabled=True,
        )
        wrong_path = reverse(
            "customers:client_invitation_claim",
            args=[other_business.slug, invitation.id, raw_token],
        )

        response = self.client_class().get(wrong_path)

        self.assertEqual(response.status_code, 410)
        self.assertFalse(
            BusinessClientAccess.objects.filter(business_client=self.business_client).exists()
        )

    def test_expired_invitation_fails_closed(self):
        response, invitation = self._create_invitation()
        claim_path = urlparse(response.context["invitation_url"]).path
        BusinessClientAccessInvitation.objects.filter(pk=invitation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.client_class().get(claim_path)

        self.assertEqual(response.status_code, 410)
        self.assertFalse(
            BusinessClientAccess.objects.filter(business_client=self.business_client).exists()
        )

    def test_paused_business_does_not_accept_a_new_invitation_claim(self):
        response, _ = self._create_invitation()
        claim_path = urlparse(response.context["invitation_url"]).path
        self.business.is_active = False
        self.business.public_booking_enabled = False
        self.business.save(update_fields=["is_active", "public_booking_enabled", "updated_at"])

        blocked = self.client_class().get(claim_path)

        self.assertEqual(blocked.status_code, 404)
        self.assertFalse(
            BusinessClientAccess.objects.filter(business_client=self.business_client).exists()
        )


class ProfessionalClientViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        cls.business = Business.objects.get(slug="peluqueria-mari")
        cls.other_business = Business.objects.get(slug="barberia-norte")
        cls.professional = get_user_model().objects.get(normalized_phone="+34600111001")

    def test_professional_client_list_requires_login(self):
        response = self.client.get(reverse("customers:professional_client_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_quick_client_rechecks_compliance_after_the_form_was_built(self):
        business = Business.objects.get(pk=self.business.pk)
        business.legal_compliance_enabled = False
        business.save(update_fields=["legal_compliance_enabled", "updated_at"])
        form = ProfessionalClientQuickForm(
            {
                "full_name": "Cliente durante cambio legal",
                "phone": "600333118",
            },
            business=business,
        )
        self.assertTrue(form.is_valid(), form.errors)
        Business.objects.filter(pk=self.business.pk).update(
            legal_compliance_enabled=True
        )

        with self.assertRaisesMessage(
            ValidationError,
            LEGAL_PRESENTATION_CHANGED_MESSAGE,
        ):
            form.save(recorded_by=self.professional, legal_presentation_token="")

        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente durante cambio legal",
            ).exists()
        )

    def test_quick_client_rerenders_fresh_compliance_after_false_to_true_flip(self):
        business = Business.objects.get(pk=self.business.pk)
        business.legal_compliance_enabled = False
        business.save(update_fields=["legal_compliance_enabled", "updated_at"])
        self.client.force_login(self.professional)
        original_is_valid = ProfessionalClientQuickForm.is_valid

        def validate_then_enable_compliance(form):
            is_valid = original_is_valid(form)
            Business.objects.filter(pk=business.pk).update(
                legal_compliance_enabled=True
            )
            return is_valid

        with patch.object(
            ProfessionalClientQuickForm,
            "is_valid",
            new=validate_then_enable_compliance,
        ):
            response = self.client.post(
                reverse("customers:professional_client_list"),
                {
                    "full_name": "Cliente durante render legal",
                    "phone": "600333116",
                    "legal_presentation_token": "",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, LEGAL_PRESENTATION_CHANGED_MESSAGE)
        self.assertTrue(response.context["business"].legal_compliance_enabled)
        self.assertIsNotNone(response.context["privacy_document"])
        self.assertTrue(response.context["legal_presentation_token"])
        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente durante render legal",
            ).exists()
        )

    def test_rotated_quick_client_receipt_with_another_error_clears_confirmation(self):
        self.client.force_login(self.professional)
        list_url = reverse("customers:professional_client_list")
        page = self.client.get(list_url)
        old_token = page.context["legal_presentation_token"]
        ordinary_error = self.client.post(
            list_url,
            {
                "full_name": "Cliente con recibo rotado",
                "phone": "600333117",
                "email": "correo-no-valido",
                "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
                "privacy_information_provided": "on",
                "legal_presentation_token": old_token,
            },
        )
        self.assertEqual(ordinary_error.status_code, 200)
        self.assertTrue(
            ordinary_error.context["quick_form"][
                "privacy_information_provided"
            ].value()
        )
        self.assertEqual(
            ordinary_error.context["legal_presentation_token"],
            old_token,
        )
        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente con recibo rotado",
            ).exists()
        )

        old_document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
        LegalDocument.objects.filter(pk=old_document.pk).update(is_active=False)
        replacement = LegalDocument.objects.create(
            kind=old_document.kind,
            slug="privacidad-clientes-alta-rapida-b",
            version="quick-rotation-b",
            title=old_document.title,
            lead=old_document.lead,
            sections=old_document.sections,
            is_active=True,
        )

        response = self.client.post(
            list_url,
            {
                "full_name": "Cliente con recibo rotado",
                "phone": "600333117",
                "email": "correo-no-valido",
                "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
                "privacy_information_provided": "on",
                "legal_presentation_token": old_token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, LEGAL_PRESENTATION_CHANGED_MESSAGE)
        self.assertContains(response, f"versión {replacement.version}")
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'data-error-summary')
        self.assertFalse(
            response.context["quick_form"]["privacy_information_provided"].value()
        )
        self.assertNotEqual(response.context["legal_presentation_token"], old_token)
        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente con recibo rotado",
            ).exists()
        )

        reconfirmation_required = self.client.post(
            list_url,
            {
                "full_name": "Cliente con recibo rotado",
                "phone": "600333117",
                "email": "cliente.rotado@example.com",
                "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
                "legal_presentation_token": response.context[
                    "legal_presentation_token"
                ],
            },
        )
        self.assertEqual(reconfirmation_required.status_code, 200)
        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente con recibo rotado",
            ).exists()
        )

    def test_professional_client_list_shows_business_clients(self):
        BusinessClient.objects.create(
            business=self.other_business,
            full_name="María Ajena",
        )
        self.client.force_login(self.professional)

        response = self.client.get(
            reverse("customers:professional_client_list"),
            {"q": "María"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clientes de Peluquería Mari")
        self.assertContains(response, "María López")
        self.assertContains(response, "Guardar cliente")
        self.assertContains(response, "Información al cliente")
        self.assertContains(
            response,
            reverse("legal:business_privacy", args=[self.business.slug]),
        )
        self.assertNotContains(response, "Javier Martín")
        self.assertNotContains(response, "María Ajena")
        self.assertTrue(
            all(client.business_id == self.business.pk for client in response.context["clients"])
        )

    def test_professional_client_list_paginates_six_at_a_time_and_preserves_filters(self):
        for index in range(7):
            BusinessClient.objects.create(
                business=self.business,
                full_name=f"Cliente extra {index + 1:02d}",
            )
        self.client.force_login(self.professional)
        list_url = reverse("customers:professional_client_list")

        first_page = self.client.get(
            list_url,
            {"status": "all", "q": "Cliente", "page": 1},
        )

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(len(first_page.context["clients"]), 6)
        self.assertEqual(first_page.context["clients_page"].paginator.count, 7)
        self.assertContains(first_page, "Página 1 de 2")
        self.assertContains(
            first_page,
            "?status=all&amp;q=Cliente&amp;page=2",
        )

        second_page = self.client.get(
            list_url,
            {"status": "all", "q": "Cliente", "page": 2},
        )

        self.assertEqual(len(second_page.context["clients"]), 1)
        self.assertContains(second_page, "Página 2 de 2")
        self.assertContains(
            second_page,
            "?status=all&amp;q=Cliente&amp;page=1",
        )

    def test_professional_can_create_client_from_client_list(self):
        self.client.force_login(self.professional)
        list_url = reverse("customers:professional_client_list")
        page = self.client.get(list_url)

        response = self.client.post(
            list_url,
            {
                "full_name": "Paula Vega",
                "phone": "600333111",
                "email": "paula@example.com",
                "internal_notes": "Prefiere primera hora.",
                "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
                "privacy_information_provided": "on",
                "legal_presentation_token": page.context["legal_presentation_token"],
            },
        )

        client = BusinessClient.objects.get(business=self.business, full_name="Paula Vega")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"], reverse("customers:professional_client_detail", args=[client.id])
        )
        self.assertEqual(client.phone_normalized, "+34600333111")
        self.assertEqual(client.source, BusinessClient.Source.PROFESSIONAL)
        evidence = CustomerPrivacyEvidence.objects.get(business_client=client)
        self.assertEqual(evidence.channel, CustomerPrivacyEvidence.Channel.PHONE)
        self.assertEqual(evidence.recorded_by, self.professional)
        self.assertEqual(
            evidence.informed_party_type,
            CustomerPrivacyEvidence.InformedParty.CLIENT,
        )
        self.assertEqual(evidence.informed_party_name_snapshot, "Paula Vega")

    def test_professional_quick_client_requires_privacy_information(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("customers:professional_client_list"),
            {
                "full_name": "Cliente sin constancia",
                "phone": "600333119",
                "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Confirma que el cliente o su persona autorizada ha recibido la información.",
        )
        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente sin constancia",
            ).exists()
        )

    def test_professional_can_create_profile_without_own_phone(self):
        self.client.force_login(self.professional)
        list_url = reverse("customers:professional_client_list")
        page = self.client.get(list_url)

        response = self.client.post(
            list_url,
            {
                "full_name": "Leo López",
                "phone": "",
                "email": "",
                "internal_notes": "Menor gestionado por su madre.",
                "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "privacy_information_provided": "on",
                "legal_presentation_token": page.context["legal_presentation_token"],
            },
        )

        profile = BusinessClient.objects.get(business=self.business, full_name="Leo López")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(profile.phone, "")
        self.assertEqual(profile.phone_normalized, "")

    def test_new_profile_can_include_registered_authorized_person(self):
        self.client.force_login(self.professional)
        authorized_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        list_url = reverse("customers:professional_client_list")
        page = self.client.get(list_url)

        response = self.client.post(
            list_url,
            {
                "full_name": "Leo López",
                "phone": "",
                "email": "",
                "internal_notes": "Menor gestionado por su madre.",
                "authorized_business_client": authorized_client.id,
                "authorized_client_search": authorized_client.full_name,
                "authorized_relationship": BusinessClientAuthorizedContact.Relationship.MOTHER,
                "authorized_allow_online": "on",
                "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "privacy_information_provided": "on",
                "legal_presentation_token": page.context["legal_presentation_token"],
            },
        )

        profile = BusinessClient.objects.get(business=self.business, full_name="Leo López")
        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[profile.id]),
        )
        contact = profile.authorized_contacts.get()
        self.assertEqual(contact.linked_business_client, authorized_client)
        self.assertEqual(contact.phone_normalized, authorized_client.phone_normalized)
        self.assertTrue(contact.is_active)
        self.assertTrue(contact.is_primary_contact)
        self.assertTrue(
            BusinessClientAccessGrant.objects.filter(
                access=authorized_client.access,
                business_client=profile,
                authorized_contact=contact,
                is_active=True,
            ).exists()
        )
        evidence = CustomerPrivacyEvidence.objects.get(business_client=profile)
        self.assertEqual(
            evidence.informed_party_type,
            CustomerPrivacyEvidence.InformedParty.AUTHORIZED_PERSON,
        )
        self.assertEqual(evidence.informed_party_name_snapshot, authorized_client.full_name)

    def test_reused_quick_client_rejects_different_optional_data_before_evidence(self):
        self.client.force_login(self.professional)
        list_url = reverse("customers:professional_client_list")

        for index, changed_field in enumerate(("email", "internal_notes"), start=1):
            with self.subTest(changed_field=changed_field):
                existing = BusinessClient.objects.create(
                    business=self.business,
                    full_name=f"Cliente existente {index}",
                    phone=f"60033345{index}",
                    email=f"existente{index}@example.com",
                    internal_notes=f"Notas existentes {index}",
                    source=BusinessClient.Source.PROFESSIONAL,
                )
                page = self.client.get(list_url)
                payload = {
                    "full_name": existing.full_name,
                    "phone": existing.phone,
                    "email": existing.email,
                    "internal_notes": existing.internal_notes,
                    "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
                    "privacy_information_provided": "on",
                    "legal_presentation_token": page.context[
                        "legal_presentation_token"
                    ],
                }
                payload[changed_field] = (
                    f"distinto{index}@example.com"
                    if changed_field == "email"
                    else f"Notas distintas {index}"
                )

                response = self.client.post(list_url, payload)

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    "el correo o las notas no coinciden",
                )
                self.assertEqual(
                    CustomerPrivacyEvidenceEvent.objects.filter(
                        business_client=existing
                    ).count(),
                    0,
                )
                self.assertEqual(
                    CustomerPrivacyEvidence.objects.filter(
                        business_client=existing
                    ).count(),
                    0,
                )
                existing.refresh_from_db()
                self.assertEqual(existing.email, f"existente{index}@example.com")
                self.assertEqual(
                    existing.internal_notes,
                    f"Notas existentes {index}",
                )

    def test_reused_quick_client_exact_replay_is_idempotent(self):
        self.client.force_login(self.professional)
        existing = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente existente replay",
            phone="600333459",
            email="existente-replay@example.com",
            internal_notes="Datos ya guardados.",
            source=BusinessClient.Source.PROFESSIONAL,
        )
        list_url = reverse("customers:professional_client_list")
        page = self.client.get(list_url)
        payload = {
            "full_name": existing.full_name,
            "phone": existing.phone,
            "email": existing.email,
            "internal_notes": existing.internal_notes,
            "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
            "privacy_information_provided": "on",
            "legal_presentation_token": page.context["legal_presentation_token"],
        }

        first = self.client.post(list_url, payload)
        second = self.client.post(list_url, payload)

        expected_location = reverse(
            "customers:professional_client_detail",
            args=[existing.pk],
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first["Location"], expected_location)
        self.assertEqual(second["Location"], expected_location)
        self.assertEqual(
            BusinessClient.objects.filter(
                business=self.business,
                full_name=existing.full_name,
                phone_normalized=existing.phone_normalized,
            ).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=existing
            ).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(
                business_client=existing
            ).count(),
            1,
        )

    def test_repeating_the_same_quick_client_receipt_is_fully_idempotent(self):
        self.client.force_login(self.professional)
        authorized_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        list_url = reverse("customers:professional_client_list")
        page = self.client.get(list_url)
        payload = {
            "full_name": "Leo Replay",
            "phone": "",
            "email": "",
            "internal_notes": "Menor gestionado por su madre.",
            "authorized_business_client": authorized_client.id,
            "authorized_client_search": authorized_client.full_name,
            "authorized_relationship": (
                BusinessClientAuthorizedContact.Relationship.MOTHER
            ),
            "authorized_allow_online": "on",
            "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
            "privacy_information_provided": "on",
            "legal_presentation_token": page.context["legal_presentation_token"],
        }

        first = self.client.post(list_url, payload)
        second = self.client.post(list_url, payload)

        profile = BusinessClient.objects.get(
            business=self.business,
            full_name="Leo Replay",
        )
        expected_location = reverse(
            "customers:professional_client_detail",
            args=[profile.pk],
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first["Location"], expected_location)
        self.assertEqual(second["Location"], expected_location)
        self.assertEqual(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Leo Replay",
            ).count(),
            1,
        )
        self.assertEqual(profile.authorized_contacts.count(), 1)
        self.assertEqual(
            BusinessClientAccessGrant.objects.filter(
                business=self.business,
                business_client=profile,
                is_active=True,
            ).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(business_client=profile).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=profile
            ).count(),
            1,
        )

    def test_quick_client_receipt_cannot_be_reused_with_other_data(self):
        self.client.force_login(self.professional)
        list_url = reverse("customers:professional_client_list")
        page = self.client.get(list_url)
        token = page.context["legal_presentation_token"]
        payload = {
            "full_name": "Cliente Replay Original",
            "phone": "600333441",
            "email": "",
            "internal_notes": "",
            "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
            "privacy_information_provided": "on",
            "legal_presentation_token": token,
        }
        first = self.client.post(list_url, payload)
        profile = BusinessClient.objects.get(
            business=self.business,
            full_name="Cliente Replay Original",
        )

        altered = self.client.post(
            list_url,
            {**payload, "full_name": "Cliente Replay Alterado"},
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(altered.status_code, 200)
        self.assertContains(
            altered,
            "No podemos reutilizar esta confirmación con otros datos",
        )
        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente Replay Alterado",
            ).exists()
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=profile
            ).count(),
            1,
        )
        self.assertFalse(
            altered.context["quick_form"]["privacy_information_provided"].value()
        )
        self.assertNotEqual(altered.context["legal_presentation_token"], token)

    def test_quick_client_rechecks_that_authorized_person_is_still_active(self):
        self.client.force_login(self.professional)
        authorized_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        page = self.client.get(reverse("customers:professional_client_list"))
        form = ProfessionalClientQuickForm(
            {
                "full_name": "Cliente tras pausa autorizada",
                "phone": "",
                "email": "",
                "authorized_business_client": authorized_client.pk,
                "authorized_client_search": authorized_client.full_name,
                "authorized_relationship": (
                    BusinessClientAuthorizedContact.Relationship.MOTHER
                ),
                "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "privacy_information_provided": "on",
            },
            business=self.business,
        )
        self.assertTrue(form.is_valid(), form.errors)
        contact_count = BusinessClientAuthorizedContact.objects.filter(
            business=self.business
        ).count()
        grant_count = BusinessClientAccessGrant.objects.filter(
            business=self.business
        ).count()
        projection_count = CustomerPrivacyEvidence.objects.filter(
            business=self.business
        ).count()
        event_count = CustomerPrivacyEvidenceEvent.objects.filter(
            business=self.business
        ).count()
        BusinessClient.objects.filter(pk=authorized_client.pk).update(is_active=False)

        with self.assertRaisesMessage(
            ValidationError,
            "La persona autorizada ya no está activa",
        ):
            form.save(
                recorded_by=self.professional,
                legal_presentation_token=page.context["legal_presentation_token"],
            )

        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente tras pausa autorizada",
            ).exists()
        )
        self.assertEqual(
            BusinessClientAuthorizedContact.objects.filter(
                business=self.business
            ).count(),
            contact_count,
        )
        self.assertEqual(
            BusinessClientAccessGrant.objects.filter(
                business=self.business
            ).count(),
            grant_count,
        )
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(business=self.business).count(),
            projection_count,
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(business=self.business).count(),
            event_count,
        )

    def test_quick_client_rechecks_authorized_online_access_before_writing(self):
        self.client.force_login(self.professional)
        authorized_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        page = self.client.get(reverse("customers:professional_client_list"))
        form = ProfessionalClientQuickForm(
            {
                "full_name": "Cliente tras pausa de cuenta",
                "phone": "",
                "email": "",
                "authorized_business_client": authorized_client.pk,
                "authorized_client_search": authorized_client.full_name,
                "authorized_relationship": (
                    BusinessClientAuthorizedContact.Relationship.MOTHER
                ),
                "authorized_allow_online": "on",
                "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "privacy_information_provided": "on",
            },
            business=self.business,
        )
        self.assertTrue(form.is_valid(), form.errors)
        contact_count = BusinessClientAuthorizedContact.objects.filter(
            business=self.business
        ).count()
        grant_count = BusinessClientAccessGrant.objects.filter(
            business=self.business
        ).count()
        projection_count = CustomerPrivacyEvidence.objects.filter(
            business=self.business
        ).count()
        event_count = CustomerPrivacyEvidenceEvent.objects.filter(
            business=self.business
        ).count()
        BusinessClientAccess.objects.filter(
            business=self.business,
            business_client=authorized_client,
        ).update(is_active=False)

        with self.assertRaisesMessage(
            ValidationError,
            "La cuenta online de la persona autorizada ya no está activa",
        ):
            form.save(
                recorded_by=self.professional,
                legal_presentation_token=page.context["legal_presentation_token"],
            )

        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente tras pausa de cuenta",
            ).exists()
        )
        self.assertEqual(
            BusinessClientAuthorizedContact.objects.filter(
                business=self.business
            ).count(),
            contact_count,
        )
        self.assertEqual(
            BusinessClientAccessGrant.objects.filter(
                business=self.business
            ).count(),
            grant_count,
        )
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(business=self.business).count(),
            projection_count,
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(business=self.business).count(),
            event_count,
        )

    def test_quick_client_hides_the_form_when_privacy_document_is_missing(self):
        LegalDocument.objects.filter(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        ).update(is_active=False)
        self.client.force_login(self.professional)
        list_url = reverse("customers:professional_client_list")

        page = self.client.get(list_url)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "No hemos guardado ningún dato")
        self.assertNotContains(page, "Guardar cliente")
        self.assertNotContains(page, 'class="quick-client-card')
        self.assertEqual(page.context["legal_presentation_token"], "")

        rejected = self.client.post(
            list_url,
            {
                "full_name": "Cliente sin política",
                "phone": "600333442",
                "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
                "privacy_information_provided": "on",
                "legal_presentation_token": "recibo-forjado",
            },
        )
        self.assertEqual(rejected.status_code, 503)
        self.assertFalse(
            BusinessClient.objects.filter(
                business=self.business,
                full_name="Cliente sin política",
            ).exists()
        )

    def test_disabled_legal_control_explains_preserved_privacy_history(self):
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucía Gómez",
        )
        self.assertTrue(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=business_client,
            ).exists()
        )
        Business.objects.filter(pk=self.business.pk).update(
            legal_compliance_enabled=False,
        )
        self.client.force_login(self.professional)

        page = self.client.get(
            reverse(
                "customers:professional_client_detail",
                args=[business_client.pk],
            )
        )

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Control legal no requerido")
        self.assertContains(page, "La constancia anterior se conserva en el historial")
        self.assertContains(page, "AgendaSalon no te pedirá registrar una nueva")
        self.assertNotContains(page, "no hay una política vigente disponible")
        self.assertNotContains(page, "Guardar constancia")

    def test_disabled_legal_control_explains_state_without_privacy_history(self):
        business_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente sin historial con control desactivado",
            phone="600333445",
        )
        Business.objects.filter(pk=self.business.pk).update(
            legal_compliance_enabled=False,
        )
        self.client.force_login(self.professional)

        page = self.client.get(
            reverse(
                "customers:professional_client_detail",
                args=[business_client.pk],
            )
        )

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Control legal no requerido")
        self.assertContains(
            page,
            "AgendaSalon no te pedirá registrar una constancia en esta ficha",
        )
        self.assertNotContains(page, "la política no está disponible")
        self.assertNotContains(page, "Guardar constancia")

    def test_manual_privacy_record_is_unavailable_without_current_document(self):
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucía Gómez",
        )
        client_without_history = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente sin historial legal",
            phone="600333444",
        )
        projection_count = CustomerPrivacyEvidence.objects.filter(
            business_client=business_client
        ).count()
        event_count = CustomerPrivacyEvidenceEvent.objects.filter(
            business_client=business_client
        ).count()
        LegalDocument.objects.filter(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        ).update(is_active=False)
        self.client.force_login(self.professional)
        detail_url = reverse(
            "customers:professional_client_detail",
            args=[business_client.pk],
        )

        page = self.client.get(detail_url)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "No hemos guardado ninguna constancia")
        self.assertContains(
            page,
            "Se conserva en el historial, aunque ahora no hay una política vigente",
        )
        self.assertNotContains(page, "Guardar constancia")
        no_history_page = self.client.get(
            reverse(
                "customers:professional_client_detail",
                args=[client_without_history.pk],
            )
        )
        self.assertContains(
            no_history_page,
            "No hay una constancia vigente porque la política no está disponible",
        )
        self.assertNotContains(
            no_history_page,
            "No consta todavía la entrega de la información vigente",
        )
        rejected = self.client.post(
            reverse(
                "customers:professional_client_privacy_record",
                args=[business_client.pk],
            ),
            {
                "channel": CustomerPrivacyEvidence.Channel.PHONE,
                "legal_presentation_token": "recibo-forjado",
            },
            follow=True,
        )
        self.assertContains(rejected, "No hemos guardado ninguna constancia")
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(
                business_client=business_client
            ).count(),
            projection_count,
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=business_client
            ).count(),
            event_count,
        )

    def test_manual_privacy_record_rechecks_disabled_compliance_before_writing(self):
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucía Gómez",
        )
        projection_count = CustomerPrivacyEvidence.objects.filter(
            business_client=business_client
        ).count()
        event_count = CustomerPrivacyEvidenceEvent.objects.filter(
            business_client=business_client
        ).count()
        self.client.force_login(self.professional)
        detail_url = reverse(
            "customers:professional_client_detail",
            args=[business_client.pk],
        )
        page = self.client.get(detail_url)
        token = page.context["legal_presentation_token"]
        Business.objects.filter(pk=self.business.pk).update(
            legal_compliance_enabled=False
        )

        rejected = self.client.post(
            reverse(
                "customers:professional_client_privacy_record",
                args=[business_client.pk],
            ),
            {
                "channel": CustomerPrivacyEvidence.Channel.PHONE,
                "legal_presentation_token": token,
            },
            follow=True,
        )

        self.assertContains(rejected, "El control de privacidad está desactivado")
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(
                business_client=business_client
            ).count(),
            projection_count,
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=business_client
            ).count(),
            event_count,
        )

    def test_manual_privacy_receipt_cannot_be_reused_with_another_channel(self):
        business_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente constancia replay",
            phone="600333443",
        )
        self.client.force_login(self.professional)
        detail_url = reverse(
            "customers:professional_client_detail",
            args=[business_client.pk],
        )
        page = self.client.get(detail_url)
        token = page.context["legal_presentation_token"]
        record_url = reverse(
            "customers:professional_client_privacy_record",
            args=[business_client.pk],
        )

        first = self.client.post(
            record_url,
            {
                "channel": CustomerPrivacyEvidence.Channel.PHONE,
                "legal_presentation_token": token,
            },
        )
        altered = self.client.post(
            record_url,
            {
                "channel": CustomerPrivacyEvidence.Channel.EMAIL,
                "legal_presentation_token": token,
            },
            follow=True,
        )

        self.assertEqual(first.status_code, 302)
        self.assertContains(
            altered,
            "No podemos reutilizar esta confirmación con otros datos",
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=business_client
            ).count(),
            1,
        )
        evidence = CustomerPrivacyEvidence.objects.get(
            business_client=business_client
        )
        self.assertEqual(evidence.channel, CustomerPrivacyEvidence.Channel.PHONE)

    def test_professional_client_lookup_is_scoped_and_reports_online_status(self):
        self.client.force_login(self.professional)

        response = self.client.get(
            reverse("customers:professional_client_lookup"),
            {"q": "María"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()["results"]
        self.assertEqual([result["name"] for result in payload], ["María López"])
        self.assertEqual(payload[0]["online_status"], "active")
        self.assertNotIn("Javier Martín", [result["name"] for result in payload])

    def test_professional_client_detail_is_scoped_to_business(self):
        self.client.force_login(self.professional)
        client = BusinessClient.objects.get(business=self.business, full_name="Lucía Gómez")
        other_client = BusinessClient.objects.get(
            business=self.other_business, full_name="Javier Martín"
        )

        response = self.client.get(
            reverse("customers:professional_client_detail", args=[client.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ficha de cliente")
        self.assertContains(response, "Lucía Gómez")
        self.assertContains(response, "Próximas citas")
        self.assertContains(response, "Historial")
        self.assertContains(response, "Personas autorizadas")

        response = self.client.get(
            reverse("customers:professional_client_detail", args=[other_client.id])
        )
        self.assertEqual(response.status_code, 404)

    def test_quick_client_from_appointment_assistant_selects_new_client(self):
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")
        page = self.client.get(assistant_url)

        response = self.client.post(
            assistant_url,
            {
                "action": "quick_client",
                "full_name": "Nuria Soler",
                "phone": "600333222",
                "manual_channel": "telefono",
                "target_date": "2026-07-09",
                "selected_work_line_id": "8",
                "selected_starts_at": "2026-07-09T18:30:00+02:00",
                "privacy_channel": CustomerPrivacyEvidence.Channel.WHATSAPP,
                "privacy_information_provided": "on",
                "legal_presentation_token": page.context[
                    "quick_legal_presentation_token"
                ],
            },
        )

        client = BusinessClient.objects.get(business=self.business, full_name="Nuria Soler")
        self.assertEqual(response.status_code, 302)
        redirect_query = parse_qs(urlparse(response["Location"]).query)
        self.assertEqual(redirect_query["business_client"], [str(client.id)])
        self.assertEqual(redirect_query["manual_channel"], ["telefono"])
        self.assertEqual(redirect_query["target_date"], ["2026-07-09"])
        self.assertEqual(redirect_query["selected_work_line_id"], ["8"])
        self.assertEqual(
            redirect_query["selected_starts_at"],
            ["2026-07-09T18:30:00+02:00"],
        )
        evidence = CustomerPrivacyEvidence.objects.get(business_client=client)
        self.assertEqual(evidence.channel, CustomerPrivacyEvidence.Channel.WHATSAPP)

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False)
    def test_professional_edit_form_is_preloaded(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        response = self.client.get(
            reverse("customers:professional_client_edit", args=[business_client.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="María López"')
        self.assertContains(response, 'value="600111201"')
        self.assertContains(response, 'readonly aria-readonly="true"')
        self.assertContains(response, "Solo lectura mientras el correo esté desactivado")
        self.assertContains(response, "El resto de la ficha sigue siendo editable")
        self.assertNotContains(response, "Si cambias el correo, se cerrarán sus sesiones")

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True)
    def test_professional_edit_keeps_email_change_guidance_when_delivery_is_enabled(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )

        response = self.client.get(
            reverse("customers:professional_client_edit", args=[business_client.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Si cambias el correo, se cerrarán sus sesiones")
        self.assertNotContains(response, "Solo lectura mientras el correo esté desactivado")
        self.assertNotContains(response, 'readonly aria-readonly="true"')

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False)
    def test_demo_blocks_only_online_email_change_without_mutating_the_client(self):
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        access = business_client.access
        original_email = access.email
        original_password_hash = access.password_hash
        original_verified_at = access.email_verified_at
        queued_before = OutboundEmail.objects.filter(
            client_access=access,
            kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
        ).count()
        self.client.force_login(self.professional)
        edit_url = reverse(
            "customers:professional_client_edit",
            args=[business_client.id],
        )

        blocked = self.client.post(
            edit_url,
            {
                "full_name": "María bloqueada",
                "phone": "600 333 445",
                "email": "maria.bloqueada@example.com",
                "internal_notes": "Este cambio no debe persistir.",
            },
        )

        self.assertEqual(blocked.status_code, 200)
        self.assertContains(blocked, "no puede cambiarse mientras el envío")
        self.assertContains(blocked, "Los demás datos sí pueden editarse")
        business_client.refresh_from_db()
        access.refresh_from_db()
        self.assertEqual(business_client.full_name, "María López")
        self.assertEqual(access.email, original_email)
        self.assertEqual(access.password_hash, original_password_hash)
        self.assertEqual(access.email_verified_at, original_verified_at)
        self.assertEqual(
            OutboundEmail.objects.filter(
                client_access=access,
                kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
            ).count(),
            queued_before,
        )

        allowed = self.client.post(
            edit_url,
            {
                "full_name": "María López Romero",
                "phone": "600 333 445",
                "email": original_email,
                "internal_notes": "Los demás datos sí quedan editados.",
            },
        )

        self.assertRedirects(
            allowed,
            reverse(
                "customers:professional_client_detail",
                args=[business_client.id],
            ),
        )
        business_client.refresh_from_db()
        access.refresh_from_db()
        self.assertEqual(business_client.full_name, "María López Romero")
        self.assertEqual(business_client.phone, "600 333 445")
        self.assertEqual(business_client.internal_notes, "Los demás datos sí quedan editados.")
        self.assertEqual(access.email, original_email)
        self.assertEqual(access.password_hash, original_password_hash)
        self.assertEqual(access.email_verified_at, original_verified_at)

    def test_professional_edit_with_same_access_email_preserves_security_state(self):
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        access = business_client.access
        login_url = reverse("customers:client_access", args=[self.business.slug])
        customer_browser = Client()
        self.assertEqual(
            customer_browser.post(
                login_url,
                {
                    "identifier": access.email,
                    "password": "AgendaSalonDemo2",
                },
            ).status_code,
            302,
        )
        reset_path = urlparse(client_password_reset_url(access)).path
        self.assertEqual(Client().get(reset_path).status_code, 200)
        old_password_hash = access.password_hash
        old_verified_at = access.email_verified_at
        old_session_fingerprint = customer_browser.session[CLIENT_ACCESS_PASSWORD_SESSION_KEY]
        queued_before = OutboundEmail.objects.filter(
            client_access=access,
            kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
        ).count()
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("customers:professional_client_edit", args=[business_client.id]),
            {
                "full_name": business_client.full_name,
                "phone": business_client.phone,
                "email": access.email,
                "internal_notes": "Nota actualizada sin rotar identidad.",
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[business_client.id]),
        )
        business_client.refresh_from_db()
        access.refresh_from_db()
        self.assertEqual(business_client.email, access.email)
        self.assertEqual(access.password_hash, old_password_hash)
        self.assertEqual(access.email_verified_at, old_verified_at)
        self.assertEqual(customer_browser.get(login_url).status_code, 302)
        self.assertEqual(
            customer_browser.session[CLIENT_ACCESS_PASSWORD_SESSION_KEY],
            old_session_fingerprint,
        )
        self.assertEqual(Client().get(reset_path).status_code, 200)
        self.assertEqual(
            OutboundEmail.objects.filter(
                client_access=access,
                kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
            ).count(),
            queued_before,
        )

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True)
    def test_professional_email_change_rotates_identity_and_queues_verification(self):
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        access = business_client.access
        old_password_hash = access.password_hash
        old_reset_path = urlparse(client_password_reset_url(access)).path
        login_url = reverse("customers:client_access", args=[self.business.slug])
        customer_browser = Client()
        self.assertEqual(
            customer_browser.post(
                login_url,
                {
                    "identifier": access.email,
                    "password": "AgendaSalonDemo2",
                },
            ).status_code,
            302,
        )
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("customers:professional_client_edit", args=[business_client.id]),
            {
                "full_name": business_client.full_name,
                "phone": business_client.phone,
                "email": "maria.nueva@example.com",
                "internal_notes": business_client.internal_notes,
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[business_client.id]),
        )
        business_client.refresh_from_db()
        access.refresh_from_db()
        self.assertEqual(business_client.email, "maria.nueva@example.com")
        self.assertEqual(access.email, "maria.nueva@example.com")
        self.assertEqual(access.email_normalized, "maria.nueva@example.com")
        self.assertIsNone(access.email_verified_at)
        self.assertNotEqual(access.password_hash, old_password_hash)
        self.assertFalse(is_password_usable(access.password_hash))
        delivery = OutboundEmail.objects.get(
            client_access=access,
            kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
            recipient_email="maria.nueva@example.com",
        )
        self.assertEqual(delivery.status, OutboundEmail.Status.PENDING)
        self.assertEqual(Client().get(old_reset_path).status_code, 410)
        self.assertEqual(customer_browser.get(login_url).status_code, 200)
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, customer_browser.session)
        self.assertNotIn(CLIENT_ACCESS_PASSWORD_SESSION_KEY, customer_browser.session)
        fresh_verification_path = urlparse(client_verification_url(access)).path
        self.assertEqual(Client().get(fresh_verification_path).status_code, 200)

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True)
    def test_second_professional_email_change_invalidates_previous_verification_link(self):
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        self.client.force_login(self.professional)
        edit_url = reverse(
            "customers:professional_client_edit",
            args=[business_client.id],
        )

        self.client.post(
            edit_url,
            {
                "full_name": business_client.full_name,
                "phone": business_client.phone,
                "email": "primero@example.com",
                "internal_notes": business_client.internal_notes,
            },
        )
        business_client.refresh_from_db()
        business_client.access.refresh_from_db()
        first_verification_path = urlparse(client_verification_url(business_client.access)).path
        self.client.post(
            edit_url,
            {
                "full_name": business_client.full_name,
                "phone": business_client.phone,
                "email": "segundo@example.com",
                "internal_notes": business_client.internal_notes,
            },
        )
        business_client.access.refresh_from_db()
        second_verification_path = urlparse(client_verification_url(business_client.access)).path

        self.assertEqual(Client().get(first_verification_path).status_code, 410)
        self.assertEqual(Client().get(second_verification_path).status_code, 200)

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True)
    def test_professional_email_change_rejects_blank_and_duplicate_without_mutation(self):
        maria = BusinessClient.objects.get(business=self.business, full_name="María López")
        lucia = BusinessClient.objects.get(business=self.business, full_name="Lucía Gómez")
        lucia.access.email = "lucia.canonica@example.com"
        lucia.access.save(update_fields=["email", "email_normalized", "updated_at"])
        lucia.email = lucia.access.email
        lucia.save(update_fields=["email", "updated_at"])
        old_email = maria.access.email
        old_password_hash = maria.access.password_hash
        old_verified_at = maria.access.email_verified_at
        self.client.force_login(self.professional)
        edit_url = reverse("customers:professional_client_edit", args=[maria.id])

        blank = self.client.post(
            edit_url,
            {
                "full_name": "María no debe cambiar",
                "phone": maria.phone,
                "email": "",
                "internal_notes": "No debe persistir.",
            },
        )
        duplicate = self.client.post(
            edit_url,
            {
                "full_name": "María tampoco debe cambiar",
                "phone": maria.phone,
                "email": lucia.access.email,
                "internal_notes": "Tampoco debe persistir.",
            },
        )

        self.assertEqual(blank.status_code, 200)
        self.assertContains(
            blank,
            "Una ficha con cuenta online debe conservar su correo electrónico.",
        )
        self.assertEqual(duplicate.status_code, 200)
        self.assertContains(
            duplicate,
            "Ese correo ya está vinculado a otra cuenta online de este negocio.",
        )
        maria.refresh_from_db()
        maria.access.refresh_from_db()
        self.assertEqual(maria.full_name, "María López")
        self.assertEqual(maria.access.email, old_email)
        self.assertEqual(maria.access.password_hash, old_password_hash)
        self.assertEqual(maria.access.email_verified_at, old_verified_at)
        self.assertFalse(
            OutboundEmail.objects.filter(
                client_access=maria.access,
                kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
            ).exists()
        )

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True)
    def test_professional_email_change_requires_active_access_and_client_file(self):
        maria = BusinessClient.objects.get(business=self.business, full_name="María López")
        lucia = BusinessClient.objects.get(business=self.business, full_name="Lucía Gómez")
        maria.access.is_active = False
        maria.access.save(update_fields=["is_active", "updated_at"])
        lucia.is_active = False
        lucia.save(update_fields=["is_active", "updated_at"])
        self.client.force_login(self.professional)

        paused_access = self.client.post(
            reverse("customers:professional_client_edit", args=[maria.id]),
            {
                "full_name": maria.full_name,
                "phone": maria.phone,
                "email": "maria.pausada@example.com",
                "internal_notes": maria.internal_notes,
            },
        )
        paused_client = self.client.post(
            reverse("customers:professional_client_edit", args=[lucia.id]),
            {
                "full_name": lucia.full_name,
                "phone": lucia.phone,
                "email": "lucia.pausada@example.com",
                "internal_notes": lucia.internal_notes,
            },
        )

        self.assertEqual(paused_access.status_code, 200)
        self.assertContains(
            paused_access,
            "Reactiva la ficha y la cuenta online antes de cambiar el correo.",
        )
        self.assertEqual(paused_client.status_code, 200)
        self.assertContains(
            paused_client,
            "Reactiva la ficha y la cuenta online antes de cambiar el correo.",
        )
        maria.access.refresh_from_db()
        lucia.access.refresh_from_db()
        self.assertNotEqual(maria.access.email, "maria.pausada@example.com")
        self.assertNotEqual(lucia.access.email, "lucia.pausada@example.com")
        self.assertFalse(
            OutboundEmail.objects.filter(
                client_access__in=[maria.access, lucia.access],
                kind=OutboundEmail.Kind.CLIENT_EMAIL_VERIFICATION,
            ).exists()
        )

    def test_professional_can_edit_client_and_sync_online_phone(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        linked_contact = business_client.authorizations_as_contact.get()
        appointments_before = business_client.appointments.count()

        response = self.client.post(
            reverse("customers:professional_client_edit", args=[business_client.id]),
            {
                "full_name": "María López Romero",
                "phone": "600 333 444",
                "email": "maria@example.com",
                "internal_notes": "Prefiere las primeras horas.",
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[business_client.id]),
        )
        business_client.refresh_from_db()
        business_client.access.refresh_from_db()
        self.assertEqual(business_client.full_name, "María López Romero")
        self.assertEqual(business_client.phone_normalized, "+34600333444")
        self.assertEqual(business_client.access.phone_normalized, "+34600333444")
        linked_contact.refresh_from_db()
        self.assertEqual(linked_contact.full_name, "María López Romero")
        self.assertEqual(linked_contact.phone_normalized, "+34600333444")
        self.assertEqual(business_client.appointments.count(), appointments_before)

    def test_professional_edit_allows_phone_used_by_other_online_account(self):
        self.client.force_login(self.professional)
        maria = BusinessClient.objects.get(business=self.business, full_name="María López")
        lucia = BusinessClient.objects.get(business=self.business, full_name="Lucía Gómez")

        response = self.client.post(
            reverse("customers:professional_client_edit", args=[maria.id]),
            {
                "full_name": maria.full_name,
                "phone": lucia.phone,
                "email": maria.access.email,
                "internal_notes": maria.internal_notes,
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[maria.id]),
        )
        maria.refresh_from_db()
        maria.access.refresh_from_db()
        self.assertEqual(maria.phone_normalized, lucia.phone_normalized)
        self.assertEqual(maria.access.phone_normalized, lucia.phone_normalized)

    def test_professional_can_pause_and_reactivate_client_without_losing_history(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Carmen Ruiz",
        )
        appointments_before = business_client.appointments.count()
        toggle_url = reverse(
            "customers:professional_client_toggle",
            args=[business_client.id],
        )

        response = self.client.post(toggle_url)

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[business_client.id]),
        )
        business_client.refresh_from_db()
        self.assertFalse(business_client.is_active)
        self.assertEqual(business_client.appointments.count(), appointments_before)
        inactive_list = self.client.get(
            reverse("customers:professional_client_list") + "?status=inactive"
        )
        self.assertContains(inactive_list, "Carmen Ruiz")

        self.client.post(toggle_url)
        business_client.refresh_from_db()
        self.assertTrue(business_client.is_active)

    def test_professional_cannot_pause_client_with_pending_confirmed_appointment(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Carmen Ruiz",
        )
        starts_at = timezone.now() + timedelta(days=30)
        Appointment.objects.create(
            business=self.business,
            business_client=business_client,
            work_line=self.business.work_lines.filter(is_active=True).first(),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )

        response = self.client.post(
            reverse("customers:professional_client_toggle", args=[business_client.id]),
            follow=True,
        )

        business_client.refresh_from_db()
        self.assertTrue(business_client.is_active)
        self.assertContains(response, "citas confirmadas pendientes")

    def test_professional_can_add_contact_and_replace_primary_contact(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucas López",
        )
        previous_primary = business_client.authorized_contacts.get(is_primary_contact=True)

        create_page = self.client.get(
            reverse("customers:professional_contact_create", args=[business_client.id])
        )
        self.assertContains(create_page, "Selecciona la relación")

        response = self.client.post(
            reverse("customers:professional_contact_create", args=[business_client.id]),
            {
                "full_name": "Carlos Gomez",
                "phone": "600111255",
                "relationship_label": BusinessClientAuthorizedContact.Relationship.FATHER,
                "is_primary_contact": "on",
                "notes": "Puede confirmar cambios de horario.",
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[business_client.id]),
        )
        new_primary = business_client.authorized_contacts.get(full_name="Carlos Gomez")
        previous_primary.refresh_from_db()
        self.assertTrue(new_primary.is_primary_contact)
        self.assertFalse(previous_primary.is_primary_contact)

    def test_professional_can_link_registered_client_as_authorized_person(self):
        self.client.force_login(self.professional)
        beneficiary = BusinessClient.objects.get(
            business=self.business,
            full_name="Carmen Ruiz",
        )
        authorized_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )

        response = self.client.post(
            reverse("customers:professional_contact_create", args=[beneficiary.id]),
            {
                "contact_type": "registered",
                "linked_business_client": authorized_client.id,
                "client_search": authorized_client.full_name,
                "relationship_label": BusinessClientAuthorizedContact.Relationship.DAUGHTER,
                "allow_online_booking": "on",
                "notes": "Gestiona sus citas.",
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[beneficiary.id]),
        )
        contact = beneficiary.authorized_contacts.get(linked_business_client=authorized_client)
        self.assertEqual(contact.full_name, authorized_client.full_name)
        self.assertEqual(contact.phone_normalized, authorized_client.phone_normalized)
        self.assertTrue(
            BusinessClientAccessGrant.objects.filter(
                access=authorized_client.access,
                business_client=beneficiary,
                authorized_contact=contact,
                is_active=True,
            ).exists()
        )

    def test_professional_cannot_link_authorized_client_from_another_business(self):
        self.client.force_login(self.professional)
        beneficiary = BusinessClient.objects.get(
            business=self.business,
            full_name="Carmen Ruiz",
        )
        other_client = BusinessClient.objects.get(
            business=self.other_business,
            full_name="Javier Martín",
        )

        response = self.client.post(
            reverse("customers:professional_contact_create", args=[beneficiary.id]),
            {
                "contact_type": "registered",
                "linked_business_client": other_client.id,
                "client_search": other_client.full_name,
                "relationship_label": BusinessClientAuthorizedContact.Relationship.FAMILY,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecciona una persona de la lista")
        self.assertFalse(
            beneficiary.authorized_contacts.filter(
                linked_business_client=other_client,
            ).exists()
        )

    def test_professional_can_edit_pause_and_reactivate_authorized_contact(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucía Gómez",
        )
        contact = BusinessClientAuthorizedContact.objects.create(
            business=self.business,
            business_client=business_client,
            full_name="Ana Gómez",
            phone="600111254",
            relationship_label=BusinessClientAuthorizedContact.Relationship.MOTHER,
            is_primary_contact=True,
            is_active=True,
        )
        edit_url = reverse(
            "customers:professional_contact_edit",
            args=[business_client.id, contact.id],
        )

        edit_page = self.client.get(edit_url)
        self.assertContains(edit_page, 'value="Ana Gómez"')
        response = self.client.post(
            edit_url,
            {
                "full_name": "Ana Gómez Ruiz",
                "phone": contact.phone,
                "relationship_label": contact.relationship_label,
                "is_primary_contact": "on",
                "notes": "Llamar si cambia la hora.",
            },
        )
        self.assertEqual(response.status_code, 302)
        contact.refresh_from_db()
        self.assertEqual(contact.full_name, "Ana Gómez Ruiz")

        toggle_url = reverse(
            "customers:professional_contact_toggle",
            args=[business_client.id, contact.id],
        )
        self.client.post(toggle_url)
        contact.refresh_from_db()
        self.assertFalse(contact.is_active)
        self.client.post(toggle_url)
        contact.refresh_from_db()
        self.assertTrue(contact.is_active)

    def test_professional_can_enable_online_booking_for_authorized_contact(self):
        self.client.force_login(self.professional)
        beneficiary = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucía Gómez",
        )
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="María López",
        )
        contact = BusinessClientAuthorizedContact.objects.create(
            business=self.business,
            business_client=beneficiary,
            linked_business_client=access.business_client,
            full_name="María López",
            phone=access.phone,
            relationship_label=BusinessClientAuthorizedContact.Relationship.MOTHER,
        )
        toggle_url = reverse(
            "customers:professional_contact_online_toggle",
            args=[beneficiary.id, contact.id],
        )

        response = self.client.post(toggle_url, follow=True)

        self.assertContains(response, "ya puede reservar online")
        grant = BusinessClientAccessGrant.objects.get(
            access=access,
            business_client=beneficiary,
        )
        self.assertTrue(grant.is_active)
        self.assertEqual(grant.authorized_contact, contact)

        self.client.post(toggle_url)
        grant.refresh_from_db()
        self.assertFalse(grant.is_active)

        self.client.post(toggle_url)
        self.client.post(
            reverse(
                "customers:professional_contact_edit",
                args=[beneficiary.id, contact.id],
            ),
            {
                "full_name": contact.full_name,
                "phone": "600111299",
                "relationship_label": contact.relationship_label,
                "notes": "Teléfono actualizado.",
            },
        )
        grant.refresh_from_db()
        self.assertFalse(grant.is_active)

    def test_reactivating_contact_does_not_restore_revoked_online_booking(self):
        beneficiary = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucía Gómez",
        )
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="María López",
        )
        contact = BusinessClientAuthorizedContact.objects.create(
            business=self.business,
            business_client=beneficiary,
            linked_business_client=access.business_client,
            full_name="María López",
            phone=access.phone,
            relationship_label=BusinessClientAuthorizedContact.Relationship.MOTHER,
        )
        grant = BusinessClientAccessGrant.objects.create(
            business=self.business,
            access=access,
            business_client=beneficiary,
            authorized_contact=contact,
            relationship_label=BusinessClientAccessGrant.Relationship.MOTHER,
            is_active=False,
        )

        set_authorized_contact_active(contact=contact, is_active=False)
        set_authorized_contact_active(contact=contact, is_active=True)

        grant.refresh_from_db()
        self.assertFalse(grant.is_active)
        self.assertIsNone(get_bookable_client(access, beneficiary.pk))

    def test_active_grant_is_temporarily_gated_by_contact_state(self):
        beneficiary = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucía Gómez",
        )
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="María López",
        )
        contact = BusinessClientAuthorizedContact.objects.create(
            business=self.business,
            business_client=beneficiary,
            linked_business_client=access.business_client,
            full_name="María López",
            phone=access.phone,
            relationship_label=BusinessClientAuthorizedContact.Relationship.MOTHER,
        )
        grant = BusinessClientAccessGrant.objects.create(
            business=self.business,
            access=access,
            business_client=beneficiary,
            authorized_contact=contact,
            relationship_label=BusinessClientAccessGrant.Relationship.MOTHER,
            is_active=True,
        )

        set_authorized_contact_active(contact=contact, is_active=False)
        grant.refresh_from_db()
        self.assertTrue(grant.is_active)
        self.assertIsNone(get_bookable_client(access, beneficiary.pk))

        set_authorized_contact_active(contact=contact, is_active=True)
        grant.refresh_from_db()
        self.assertTrue(grant.is_active)
        self.assertEqual(get_bookable_client(access, beneficiary.pk), beneficiary)

    def test_professional_contact_routes_are_scoped_to_business(self):
        self.client.force_login(self.professional)
        other_client = BusinessClient.objects.get(
            business=self.other_business,
            full_name="Javier Martín",
        )
        other_contact = BusinessClientAuthorizedContact.objects.create(
            business=self.other_business,
            business_client=other_client,
            full_name="Marta Martin",
            phone="600999101",
        )

        response = self.client.get(
            reverse(
                "customers:professional_contact_edit",
                args=[other_client.id, other_contact.id],
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_professional_can_pause_and_reactivate_online_account(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="María López",
        )
        toggle_url = reverse(
            "customers:professional_client_access_toggle",
            args=[business_client.id],
        )

        self.assertEqual(self.client.get(toggle_url).status_code, 405)
        self.client.post(toggle_url)
        business_client.access.refresh_from_db()
        self.assertFalse(business_client.access.is_active)
        self.client.post(toggle_url)
        business_client.access.refresh_from_db()
        self.assertTrue(business_client.access.is_active)

    def test_inactive_client_is_not_available_in_appointment_assistant(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Carmen Ruiz",
        )
        business_client.is_active = False
        business_client.save(update_fields=["is_active", "updated_at"])

        response = self.client.get(reverse("booking:appointment_assistant"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, ">Carmen Ruiz</option>")


# Create your tests here.

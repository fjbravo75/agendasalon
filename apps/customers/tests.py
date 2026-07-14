from datetime import timedelta
from io import StringIO
from urllib.parse import urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher, make_password
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessInvitation,
    BusinessClientAccessGrant,
    BusinessClientAuthorizedContact,
)
from apps.customers.services import (
    CLIENT_ACCESS_LAST_SEEN_SESSION_KEY,
    CLIENT_ACCESS_SESSION_KEY,
    authenticate_client_access,
    get_bookable_client,
    register_client_access,
    set_authorized_contact_active,
)
from apps.legal.models import CustomerPrivacyEvidence


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

    def test_public_registration_cannot_claim_an_existing_client_file(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="María López",
            phone="600111222",
        )

        with self.assertRaises(ValidationError):
            register_client_access(
                business=self.business,
                full_name="Otra persona",
                phone="600111222",
                email="otra@example.test",
                password="ClienteDemo2026!",
            )

        client.refresh_from_db()
        self.assertEqual(client.full_name, "María López")
        self.assertFalse(
            BusinessClientAccess.objects.filter(
                business=self.business,
                phone_normalized="+34600111222",
            ).exists()
        )

    def test_client_access_phone_is_unique_inside_business(self):
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


class ClientAccessViewTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )

    def test_client_access_page_uses_customer_copy(self):
        response = self.client.get(
            reverse("customers:client_access", args=[self.business.slug])
        )

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
        response = self.client.get(
            reverse("customers:client_register", args=[self.business.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cuenta cliente de Peluquería Mari")
        self.assertContains(response, "Crea tu cuenta")
        self.assertContains(response, "en Peluquería Mari")
        self.assertContains(response, "Crear cuenta cliente")
        self.assertContains(response, "Crear mi cuenta")
        self.assertContains(response, "Entra para reservar")
        self.assertContains(response, "client-auth-register-page")
        self.assertContains(response, "client-auth-page--salon")
        self.assertContains(
            response,
            f'href="{reverse("public_booking", args=[self.business.slug])}"',
        )
        self.assertNotContains(response, "Acceso profesional")
        self.assertNotContains(response, "Entrar y revisar reserva")

    def test_business_uses_the_selected_barbershop_visual_theme(self):
        barberia = Business.objects.create(
            commercial_name="Barbería Norte",
            slug="barberia-norte",
            is_active=True,
            public_image_preset=Business.PublicImagePreset.BARBERSHOP,
        )

        response = self.client.get(
            reverse("customers:client_access", args=[barberia.slug])
        )

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
        self.assertContains(response, "Teléfono o contraseña no válidos.")

        response = self.client.post(
            reverse("customers:client_access", args=[other_business.slug]),
            {
                "next": reverse("public_booking", args=[other_business.slug]),
                "phone": "600999222",
                "password": "ClienteDemo2026!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("public_booking", args=[other_business.slug]))

    def test_registration_waits_for_email_verification_before_booking(self):
        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "next": reverse("public_booking", args=[self.business.slug]),
                "full_name": "Cliente Web",
                "phone": "600999001",
                "email": "cliente.web@example.test",
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
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, self.client.session)

    def test_new_client_password_uses_argon2(self):
        access = register_client_access(
            business=self.business,
            full_name="Cliente Argon2",
            phone="600999010",
            email="argon2@example.test",
            password="ClienteDemo2026!",
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

    def test_registration_with_existing_phone_fails_closed_without_disclosing_the_client(self):
        BusinessClient.objects.create(
            business=self.business,
            full_name="María López",
            phone="600999002",
        )

        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Otra persona",
                "phone": "600999002",
                "email": "otra@example.test",
                "password": "ClienteDemo2026!",
                "password_confirm": "ClienteDemo2026!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "No podemos crear una cuenta con esos datos. Contacta con el negocio para activar tu acceso.",
        )
        self.assertNotContains(response, "María López")
        self.assertFalse(
            BusinessClientAccess.objects.filter(
                business=self.business,
                phone_normalized="+34600999002",
            ).exists()
        )

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
        self.assertContains(booking_response, "No necesitas una cuenta para consultar servicios y horas")
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

        response = self.client.get(
            reverse("customers:client_access", args=[self.business.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(CLIENT_ACCESS_SESSION_KEY, self.client.session)
        self.assertNotIn(CLIENT_ACCESS_LAST_SEEN_SESSION_KEY, self.client.session)


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
                "email": "invitada@example.test",
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
        self.assertEqual(identify_hasher(access.password_hash).algorithm, "argon2")
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

    def test_activation_accepts_a_valid_same_origin_csrf_submission(self):
        response, _ = self._create_invitation()
        claim_path = urlparse(response.context["invitation_url"]).path
        csrf_browser = Client(enforce_csrf_checks=True)
        csrf_browser.get(claim_path)
        activation_url = reverse(
            "customers:client_invitation_activate",
            args=[self.business.slug],
        )
        csrf_browser.get(activation_url)
        csrf_token = csrf_browser.cookies["csrftoken"].value

        response = csrf_browser.post(
            activation_url,
            {
                "csrfmiddlewaretoken": csrf_token,
                "email": "csrf@example.test",
                "password": "ClienteInvitado2026!",
                "password_confirm": "ClienteInvitado2026!",
            },
            HTTP_ORIGIN="http://testserver",
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
        self.assertFalse(BusinessClientAccess.objects.filter(business_client=self.business_client).exists())

    def test_expired_invitation_fails_closed(self):
        response, invitation = self._create_invitation()
        claim_path = urlparse(response.context["invitation_url"]).path
        BusinessClientAccessInvitation.objects.filter(pk=invitation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        response = self.client_class().get(claim_path)

        self.assertEqual(response.status_code, 410)
        self.assertFalse(BusinessClientAccess.objects.filter(business_client=self.business_client).exists())


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

    def test_professional_client_list_shows_business_clients(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("customers:professional_client_list"))

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

        response = self.client.post(
            reverse("customers:professional_client_list"),
            {
                "full_name": "Paula Vega",
                "phone": "600333111",
                "email": "paula@example.local",
                "internal_notes": "Prefiere primera hora.",
                "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
                "privacy_information_provided": "on",
            },
        )

        client = BusinessClient.objects.get(business=self.business, full_name="Paula Vega")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("customers:professional_client_detail", args=[client.id]))
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

        response = self.client.post(
            reverse("customers:professional_client_list"),
            {
                "full_name": "Leo López",
                "phone": "",
                "email": "",
                "internal_notes": "Menor gestionado por su madre.",
                "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "privacy_information_provided": "on",
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

        response = self.client.post(
            reverse("customers:professional_client_list"),
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
        other_client = BusinessClient.objects.get(business=self.other_business, full_name="Javier Martín")

        response = self.client.get(reverse("customers:professional_client_detail", args=[client.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ficha de cliente")
        self.assertContains(response, "Lucía Gómez")
        self.assertContains(response, "Próximas citas")
        self.assertContains(response, "Historial")
        self.assertContains(response, "Personas autorizadas")

        response = self.client.get(reverse("customers:professional_client_detail", args=[other_client.id]))
        self.assertEqual(response.status_code, 404)

    def test_quick_client_from_appointment_assistant_selects_new_client(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:appointment_assistant"),
            {
                "action": "quick_client",
                "full_name": "Nuria Soler",
                "phone": "600333222",
                "manual_channel": "telefono",
                "target_date": "2026-07-09",
                "privacy_channel": CustomerPrivacyEvidence.Channel.WHATSAPP,
                "privacy_information_provided": "on",
            },
        )

        client = BusinessClient.objects.get(business=self.business, full_name="Nuria Soler")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"business_client={client.id}", response["Location"])
        self.assertIn("manual_channel=telefono", response["Location"])
        self.assertIn("target_date=2026-07-09", response["Location"])
        evidence = CustomerPrivacyEvidence.objects.get(business_client=client)
        self.assertEqual(evidence.channel, CustomerPrivacyEvidence.Channel.WHATSAPP)

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
                "email": "maria@example.local",
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

    def test_professional_edit_rejects_phone_used_by_other_online_account(self):
        self.client.force_login(self.professional)
        maria = BusinessClient.objects.get(business=self.business, full_name="María López")
        lucia = BusinessClient.objects.get(business=self.business, full_name="Lucía Gómez")

        response = self.client.post(
            reverse("customers:professional_client_edit", args=[maria.id]),
            {
                "full_name": maria.full_name,
                "phone": lucia.phone,
                "email": "",
                "internal_notes": maria.internal_notes,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "otra cuenta online")
        maria.refresh_from_db()
        self.assertEqual(maria.phone_normalized, "+34600111201")

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
            full_name="Lucía Gómez",
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
        contact = business_client.authorized_contacts.get(full_name="Ana Gómez")
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

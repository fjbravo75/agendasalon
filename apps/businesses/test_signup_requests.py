from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.businesses.models import Business, BusinessSignupRequest
from apps.legal.models import LegalDocument


class BusinessSignupRequestPublicTests(TestCase):
    def setUp(self):
        self.url = reverse("business_signup_request")
        self.valid_data = {
            "business_name": "Peluquería Azahar",
            "business_type": BusinessSignupRequest.BusinessType.HAIR_SALON,
            "city": "Córdoba",
            "province": "Córdoba",
            "contact_name": "María Ruiz",
            "phone": "611 222 333",
            "email": "maria@example.com",
            "preferred_channel": BusinessSignupRequest.PreferredChannel.WHATSAPP,
            "need_text": "Quiero ordenar las citas y reducir las llamadas.",
            "privacy_acknowledged": "on",
        }

    def test_login_offers_a_path_for_a_new_professional(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, "¿Aún no tienes acceso profesional?")
        self.assertContains(response, self.url)

    def test_public_form_requires_no_account(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicita el alta")
        self.assertContains(response, "todavía no se crea ninguna cuenta")

    def test_valid_request_records_normalized_contact_and_privacy_snapshot(self):
        response = self.client.post(self.url, self.valid_data)

        self.assertRedirects(response, reverse("business_signup_request_success"))
        signup_request = BusinessSignupRequest.objects.get()
        privacy_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.PLATFORM_PRIVACY,
            is_active=True,
        )
        self.assertEqual(signup_request.normalized_phone, "+34611222333")
        self.assertEqual(signup_request.privacy_document, privacy_document)
        self.assertEqual(signup_request.privacy_document_version, privacy_document.version)
        self.assertEqual(signup_request.privacy_document_hash, privacy_document.content_hash)
        self.assertIsNotNone(signup_request.privacy_acknowledged_at)

    def test_privacy_acknowledgement_is_required(self):
        data = {**self.valid_data}
        data.pop("privacy_acknowledged")

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este campo es obligatorio")
        self.assertFalse(BusinessSignupRequest.objects.exists())

    def test_email_is_required_for_every_signup_request(self):
        data = {
            **self.valid_data,
            "email": "",
            "preferred_channel": BusinessSignupRequest.PreferredChannel.EMAIL,
        }

        response = self.client.post(self.url, data)

        self.assertContains(response, "Indica un correo para recibir la respuesta y activar el acceso")
        self.assertFalse(BusinessSignupRequest.objects.exists())

    def test_repeated_identical_request_is_idempotent_for_the_professional(self):
        first_response = self.client.post(self.url, self.valid_data)
        second_response = self.client.post(self.url, self.valid_data)

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(BusinessSignupRequest.objects.count(), 1)

    def test_rate_limit_blocks_a_fourth_daily_submission_for_the_same_phone(self):
        for index in range(3):
            response = self.client.post(
                self.url,
                {**self.valid_data, "business_name": f"Peluquería Azahar {index}"},
            )
            self.assertEqual(response.status_code, 302)

        response = self.client.post(
            self.url,
            {**self.valid_data, "business_name": "Peluquería Azahar 4"},
        )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Ya hemos recibido varios envíos", status_code=429)


class BusinessSignupRequestSuperadminTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34600999001",
            password="AdminTemporal!2026",
            full_name="Vera Administración",
        )
        privacy_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.PLATFORM_PRIVACY,
            is_active=True,
        )
        self.signup_request = BusinessSignupRequest.objects.create(
            business_name="Peluquería Azahar",
            business_type=BusinessSignupRequest.BusinessType.HAIR_SALON,
            city="Córdoba",
            province="Córdoba",
            contact_name="María Ruiz",
            phone="611 222 333",
            normalized_phone="+34611222333",
            email="maria@example.com",
            preferred_channel=BusinessSignupRequest.PreferredChannel.WHATSAPP,
            need_text="Quiero ordenar las citas.",
            privacy_document=privacy_document,
            privacy_document_version=privacy_document.version,
            privacy_document_hash=privacy_document.content_hash,
            privacy_acknowledged_at=privacy_document.published_at,
        )

    def test_request_list_is_private_to_the_superadmin(self):
        url = reverse("businesses:superadmin_signup_request_list")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.superadmin)
        response = self.client.get(url)
        self.assertContains(response, "Peluquería Azahar")
        self.assertContains(response, "1 nueva")

    def test_review_updates_status_note_and_actor(self):
        self.client.force_login(self.superadmin)
        response = self.client.post(
            reverse(
                "businesses:superadmin_signup_request_detail",
                args=[self.signup_request.pk],
            ),
            {
                "status": BusinessSignupRequest.Status.CONTACTED,
                "admin_note": "Contacto realizado. Quiere una demostración.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.signup_request.refresh_from_db()
        self.assertEqual(self.signup_request.status, BusinessSignupRequest.Status.CONTACTED)
        self.assertEqual(self.signup_request.handled_by, self.superadmin)

    def test_conversion_prefills_private_contact_without_publishing_it(self):
        self.client.force_login(self.superadmin)
        response = self.client.get(
            reverse("businesses:superadmin_business_create"),
            {"solicitud": self.signup_request.pk},
        )

        self.assertContains(response, "Peluquería Azahar")
        self.assertContains(response, "María Ruiz")
        self.assertContains(response, "El teléfono y el correo públicos quedan vacíos")

        response = self.client.post(
            reverse("businesses:superadmin_business_create"),
            {
                "signup_request_id": self.signup_request.pk,
                "commercial_name": "Peluquería Azahar",
                "slug": "peluqueria-azahar",
                "public_phone": "",
                "public_email": "",
                "address": "",
                "city": "Córdoba",
                "province": "Córdoba",
                "public_description": "",
                "is_active": "on",
                "full_name": "María Ruiz",
                "phone": "611 222 333",
                "email": "maria@example.com",
            },
        )

        business = Business.objects.get(slug="peluqueria-azahar")
        self.assertRedirects(
            response,
            reverse("businesses:superadmin_business_detail", args=[business.pk]),
        )
        self.assertEqual(business.public_phone, "")
        self.assertEqual(business.public_email, "")
        self.signup_request.refresh_from_db()
        self.assertEqual(self.signup_request.status, BusinessSignupRequest.Status.CONVERTED)
        self.assertEqual(self.signup_request.converted_business, business)
        self.assertEqual(self.signup_request.handled_by, self.superadmin)
        self.assertIsNotNone(self.signup_request.converted_at)

    def test_converted_request_cannot_be_converted_twice(self):
        business = Business.objects.create(
            commercial_name="Peluquería Azahar",
            slug="peluqueria-azahar",
        )
        self.signup_request.status = BusinessSignupRequest.Status.CONVERTED
        self.signup_request.converted_business = business
        self.signup_request.converted_at = self.signup_request.created_at
        self.signup_request.save()
        self.client.force_login(self.superadmin)

        get_response = self.client.get(
            reverse("businesses:superadmin_business_create"),
            {"solicitud": self.signup_request.pk},
        )
        self.assertRedirects(
            get_response,
            reverse(
                "businesses:superadmin_signup_request_detail",
                args=[self.signup_request.pk],
            ),
        )

        response = self.client.post(
            reverse("businesses:superadmin_business_create"),
            {
                "signup_request_id": self.signup_request.pk,
                "commercial_name": "Otro nombre",
                "slug": "otro-nombre",
                "city": "Córdoba",
                "province": "Córdoba",
                "is_active": "on",
                "full_name": "Otra persona",
                "phone": "622 222 333",
                "email": "otra@example.com",
            },
        )

        self.assertRedirects(
            response,
            reverse(
                "businesses:superadmin_signup_request_detail",
                args=[self.signup_request.pk],
            ),
        )
        self.assertFalse(Business.objects.filter(slug="otro-nombre").exists())

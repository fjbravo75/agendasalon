from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from apps.businesses.models import Business, BusinessSignupRequest
from apps.legal.models import LegalDocument
from apps.legal.presentations import LEGAL_PRESENTATION_CHANGED_MESSAGE
from apps.legal.services import platform_legal_context


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
        page = self.client.get(self.url)
        self.valid_data["legal_presentation_token"] = page.context[
            "legal_presentation_token"
        ]

    def test_login_offers_a_path_for_a_new_professional(self):
        response = self.client.get(reverse("accounts:login"))

        self.assertContains(response, "¿Aún no tienes acceso profesional?")
        self.assertContains(response, self.url)

    def test_public_form_requires_no_account(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Solicita el alta")
        self.assertContains(response, "todavía no se crea ninguna cuenta")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_public_form_accepts_a_valid_same_origin_csrf_submission(self):
        browser = Client(enforce_csrf_checks=True)
        page = browser.get(self.url, secure=True)
        csrf_token = browser.cookies["csrftoken"].value

        response = browser.post(
            self.url,
            {
                **self.valid_data,
                "csrfmiddlewaretoken": csrf_token,
                "legal_presentation_token": page.context[
                    "legal_presentation_token"
                ],
            },
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )

        self.assertEqual(page["Referrer-Policy"], "same-origin")
        self.assertRedirects(response, reverse("business_signup_request_success"))
        self.assertEqual(BusinessSignupRequest.objects.count(), 1)

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
        self.assertEqual(
            signup_request.privacy_legal_context_snapshot,
            platform_legal_context(),
        )
        self.assertIsNotNone(signup_request.privacy_acknowledged_at)

    def test_privacy_acknowledgement_is_required(self):
        data = {**self.valid_data}
        data.pop("privacy_acknowledged")

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Este campo es obligatorio")
        self.assertContains(response, "Revisa los campos indicados")
        self.assertContains(response, 'data-error-summary')
        self.assertContains(response, 'tabindex="-1"')
        self.assertContains(response, 'aria-invalid="true"')
        self.assertContains(
            response,
            'aria-describedby="id_privacy_acknowledged-error"',
        )
        self.assertContains(response, 'href="#id_privacy_acknowledged"')
        self.assertFalse(BusinessSignupRequest.objects.exists())

    def test_manipulated_legal_receipt_creates_no_request(self):
        token = self.valid_data["legal_presentation_token"]
        manipulated = f"{token[:-1]}{'x' if token[-1] != 'x' else 'y'}"

        response = self.client.post(
            self.url,
            {**self.valid_data, "legal_presentation_token": manipulated},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, LEGAL_PRESENTATION_CHANGED_MESSAGE)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertFalse(BusinessSignupRequest.objects.exists())

    def test_missing_platform_privacy_returns_503_without_recording_data(self):
        LegalDocument.objects.filter(
            kind=LegalDocument.Kind.PLATFORM_PRIVACY,
            is_active=True,
        ).update(is_active=False)

        page = self.client.get(self.url)

        self.assertEqual(page.status_code, 503)
        self.assertContains(
            page,
            "Ahora mismo no podemos mostrar la información legal necesaria",
            status_code=503,
        )
        self.assertContains(page, "No hemos guardado ningún dato", status_code=503)
        self.assertNotContains(page, "Enviar solicitud", status_code=503)
        self.assertContains(
            page,
            '<fieldset class="business-signup-form__fields" disabled>',
            status_code=503,
        )
        self.assertEqual(page.context["legal_presentation_token"], "")
        self.assertEqual(page["Cache-Control"], "no-store")
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        self.assertFalse(BusinessSignupRequest.objects.exists())

        response = self.client.post(self.url, self.valid_data)

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "No hemos guardado ningún dato", status_code=503)
        self.assertNotContains(response, "Enviar solicitud", status_code=503)
        self.assertContains(
            response,
            '<fieldset class="business-signup-form__fields" disabled>',
            status_code=503,
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertFalse(BusinessSignupRequest.objects.exists())

    def test_rotated_receipt_with_another_error_clears_the_privacy_confirmation(self):
        old_token = self.valid_data["legal_presentation_token"]
        old_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.PLATFORM_PRIVACY,
            is_active=True,
        )
        LegalDocument.objects.filter(pk=old_document.pk).update(is_active=False)
        replacement = LegalDocument.objects.create(
            kind=old_document.kind,
            slug="privacidad-plataforma-alta-negocio-b",
            version="signup-rotation-b",
            title=old_document.title,
            lead=old_document.lead,
            sections=old_document.sections,
            is_active=True,
        )

        response = self.client.post(
            self.url,
            {
                **self.valid_data,
                "email": "correo-no-valido",
                "legal_presentation_token": old_token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, LEGAL_PRESENTATION_CHANGED_MESSAGE)
        self.assertContains(response, f"versión {replacement.version}")
        self.assertContains(
            response,
            reverse("legal:platform_document", args=[replacement.slug]),
        )
        self.assertFalse(response.context["form"]["privacy_acknowledged"].value())
        self.assertNotEqual(
            response.context["legal_presentation_token"],
            old_token,
        )
        self.assertFalse(BusinessSignupRequest.objects.exists())

        reconfirmation_required = self.client.post(
            self.url,
            {
                **self.valid_data,
                "legal_presentation_token": response.context[
                    "legal_presentation_token"
                ],
                "email": "maria@example.com",
                "privacy_acknowledged": "",
            },
        )
        self.assertEqual(reconfirmation_required.status_code, 200)
        self.assertContains(reconfirmation_required, "Este campo es obligatorio")
        self.assertFalse(BusinessSignupRequest.objects.exists())

        accepted = self.client.post(
            self.url,
            {
                **self.valid_data,
                "legal_presentation_token": reconfirmation_required.context[
                    "legal_presentation_token"
                ],
            },
        )
        self.assertEqual(accepted.status_code, 302)
        signup_request = BusinessSignupRequest.objects.get()
        self.assertEqual(signup_request.privacy_document, replacement)
        self.assertEqual(
            signup_request.privacy_legal_context_snapshot,
            platform_legal_context(),
        )

    def test_email_is_required_for_every_signup_request(self):
        data = {
            **self.valid_data,
            "email": "",
            "preferred_channel": BusinessSignupRequest.PreferredChannel.EMAIL,
        }

        response = self.client.post(self.url, data)

        self.assertContains(response, "Indica un correo para recibir la respuesta y activar el acceso")
        self.assertTrue(response.context["form"]["privacy_acknowledged"].value())
        self.assertEqual(
            response.context["legal_presentation_token"],
            self.valid_data["legal_presentation_token"],
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertFalse(BusinessSignupRequest.objects.exists())

    def test_non_routable_email_is_rejected_before_creating_the_request(self):
        response = self.client.post(
            self.url,
            {**self.valid_data, "email": "maria@agenda.invalid"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usa un correo real que pueda recibir mensajes")
        self.assertNotContains(response, "Indica un correo para poder contactar por este canal")
        self.assertFalse(BusinessSignupRequest.objects.exists())

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False)
    def test_demo_email_validation_does_not_claim_that_messages_can_be_received(self):
        response = self.client.post(
            self.url,
            {**self.valid_data, "email": "maria@agenda.invalid"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Usa una dirección de correo con formato y dominio válidos")
        self.assertContains(response, "no se entregan mensajes externos")
        self.assertNotContains(response, "correo real que pueda recibir mensajes")
        self.assertFalse(BusinessSignupRequest.objects.exists())

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False)
    def test_demo_signup_form_explains_the_email_field_without_promising_delivery(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"].fields["email"].help_text,
            "Lo guardaremos como dato de contacto. En esta demostración académica "
            "no se entregan mensajes externos.",
        )
        self.assertContains(response, "Registra una solicitud de prueba")
        self.assertContains(response, "la persona responsable la revise")
        self.assertContains(response, "Registrar solicitud de prueba")
        self.assertNotContains(response, "Contactamos por el canal que elijas")
        self.assertNotContains(response, "necesarios para poder responderte")

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True)
    def test_signup_form_stays_academic_when_delivery_is_enabled(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registra una solicitud de prueba")
        self.assertContains(response, "El superadministrador revisa la solicitud")
        self.assertContains(response, "Si se aprueba, crea el negocio")
        self.assertContains(response, "Registrar solicitud de prueba")
        self.assertNotContains(response, "Contactamos por el canal que elijas")
        self.assertNotContains(response, "necesarios para poder responderte")

    def test_signup_success_copy_stays_academic_with_or_without_delivery(self):
        success_url = reverse("business_signup_request_success")

        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False):
            demo_page = self.client.get(success_url)
        with override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True):
            delivery_page = self.client.get(success_url)

        self.assertContains(demo_page, "Flujo académico completado")
        self.assertContains(demo_page, "La solicitud ha quedado registrada")
        self.assertContains(demo_page, "Todavía no se ha creado ninguna cuenta")
        self.assertNotContains(demo_page, "contactaremos contigo")
        self.assertContains(delivery_page, "Flujo académico completado")
        self.assertContains(delivery_page, "La solicitud ha quedado registrada")
        self.assertContains(delivery_page, "Todavía no se ha creado ninguna cuenta")
        self.assertNotContains(delivery_page, "contactaremos contigo")

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
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "same-origin")


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

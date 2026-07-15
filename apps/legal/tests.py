from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import Business, BusinessMembership
from apps.customers.models import BusinessClient, BusinessClientAccess
from apps.customers.services import (
    CLIENT_ACCESS_LAST_SEEN_SESSION_KEY,
    CLIENT_ACCESS_SESSION_KEY,
)
from apps.legal.models import (
    BusinessLegalProfile,
    CustomerPrivacyEvidence,
    DataRightsRequest,
    LegalAcceptance,
)
from apps.legal.services import professional_legal_status


class LegalExperienceTests(TestCase):
    def setUp(self):
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600111999",
            phone="+34600111999",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari-legal",
            public_email="mari@example.com",
            address="Calle Mayor, 10",
            city="Málaga",
            province="Málaga",
            legal_compliance_enabled=True,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
        )

    def legal_onboarding_payload(self):
        return {
            "legal_name": "María Salón, S.L.",
            "tax_identifier": "B12345678",
            "registered_address": "Calle Mayor, 10, Málaga",
            "privacy_email": "privacidad@example.com",
            "rights_contact_name": "María López",
            "retention_criteria": "Durante la relación y los plazos legales aplicables.",
            "platform_privacy_acknowledged": "on",
            "terms_accepted": "on",
            "data_processing_accepted": "on",
            "authority_declared": "on",
        }

    def complete_legal_onboarding(self):
        self.client.force_login(self.professional)
        return self.client.post(
            reverse("legal:professional_onboarding"),
            self.legal_onboarding_payload(),
            follow=True,
        )

    def test_public_legal_index_and_document_render_the_versioned_content(self):
        index_response = self.client.get(reverse("legal:legal_index"))

        self.assertEqual(index_response.status_code, 200)
        self.assertContains(index_response, "Documentación legal de AgendaSalon")
        self.assertContains(index_response, "Aviso legal")

        document_response = self.client.get(
            reverse("legal:platform_document", args=["privacidad-plataforma"])
        )
        self.assertEqual(document_response.status_code, 200)
        self.assertContains(document_response, "Privacidad de AgendaSalon")
        self.assertContains(document_response, "Huella")

    @override_settings(
        AGENDA_PLATFORM_LEGAL_NAME="AgendaSalon · demostración académica",
        AGENDA_PLATFORM_TAX_ID="",
        AGENDA_PLATFORM_LEGAL_ADDRESS="",
        AGENDA_PLATFORM_PRIVACY_EMAIL="privacidad@example.com",
        AGENDA_PLATFORM_WEBSITE="https://agendasalon.example.com",
        AGENDA_PLATFORM_LEGAL_DEMO=True,
    )
    def test_academic_demo_is_explicit_and_hides_fiscal_identity(self):
        index_response = self.client.get(reverse("legal:legal_index"))
        document_response = self.client.get(
            reverse("legal:platform_document", args=["aviso-legal"])
        )

        self.assertContains(
            index_response,
            "Demostración académica sin actividad comercial",
        )
        self.assertContains(
            document_response,
            "Demostración académica sin actividad comercial",
        )
        document_content = document_response.content.decode().lower()
        self.assertNotIn("identificación fiscal", document_content)
        self.assertNotIn("domicilio", document_content)

    def test_incomplete_business_cannot_collect_new_customer_data(self):
        response = self.client.get(
            reverse("customers:client_register", args=[self.business.slug])
        )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "está terminando su configuración", status_code=503)
        self.assertContains(response, "No se ha guardado ningún dato", status_code=503)

    def test_professional_onboarding_records_profile_and_exact_acceptances(self):
        response = self.complete_legal_onboarding()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacidad y derechos")
        profile = BusinessLegalProfile.objects.get(business=self.business)
        self.assertEqual(profile.legal_name, "María Salón, S.L.")
        acceptances = LegalAcceptance.objects.filter(
            business=self.business,
            actor_user=self.professional,
        )
        self.assertEqual(acceptances.count(), 3)
        self.assertTrue(acceptances.filter(authority_declared=True).exists())
        self.assertTrue(all(item.document_hash_snapshot for item in acceptances))

    def test_business_acceptance_is_shared_by_authorized_professionals(self):
        self.complete_legal_onboarding()
        colleague = get_user_model().objects.create_user(
            normalized_phone="+34600111888",
            phone="+34600111888",
            password="test-pass-123",
            full_name="Laura Profesional",
        )
        BusinessMembership.objects.create(business=self.business, user=colleague)

        status = professional_legal_status(colleague, self.business)

        self.assertTrue(status["is_current"])
        self.assertEqual(status["label"], "Documentación vigente")

    def test_customer_registration_records_versioned_privacy_evidence(self):
        self.complete_legal_onboarding()
        self.client.logout()
        test_password = "".join(("Una", "-clave-", "segura-", "2026"))

        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Ana Cliente",
                "phone": "600 555 111",
                "email": "ana.cliente@example.com",
                "password": test_password,
                "password_confirm": test_password,
                "privacy_acknowledged": "on",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        evidence = CustomerPrivacyEvidence.objects.get(
            business=self.business,
            business_client__full_name="Ana Cliente",
        )
        self.assertEqual(
            evidence.event_type,
            CustomerPrivacyEvidence.EventType.ACKNOWLEDGED,
        )
        self.assertEqual(
            evidence.channel,
            CustomerPrivacyEvidence.Channel.ONLINE_REGISTRATION,
        )
        self.assertEqual(evidence.document_hash_snapshot, evidence.document.content_hash)

    def test_professional_can_record_in_person_privacy_information(self):
        self.complete_legal_onboarding()
        client_record = BusinessClient.objects.create(
            business=self.business,
            full_name="Ana Cliente",
            phone="600777888",
        )

        response = self.client.post(
            reverse(
                "customers:professional_client_privacy_record",
                args=[client_record.pk],
            ),
            {"channel": CustomerPrivacyEvidence.Channel.IN_PERSON},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Información vigente")
        evidence = CustomerPrivacyEvidence.objects.get(business_client=client_record)
        self.assertEqual(evidence.recorded_by, self.professional)
        self.assertEqual(
            evidence.event_type,
            CustomerPrivacyEvidence.EventType.INFORMATION_PROVIDED,
        )

    def test_superadmin_sees_current_legal_evidence_for_the_business(self):
        self.complete_legal_onboarding()
        superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34600999000",
            phone="+34600999000",
            password="test-pass-123",
            full_name="Superadministración",
        )
        self.client.force_login(superadmin)

        detail_response = self.client.get(
            reverse("businesses:superadmin_business_detail", args=[self.business.pk])
        )
        evidence_response = self.client.get(
            reverse(
                "businesses:superadmin_business_legal_evidence",
                args=[self.business.pk],
            )
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Documentación vigente")
        self.assertEqual(evidence_response.status_code, 200)
        self.assertContains(evidence_response, "Mari Profesional")
        self.assertContains(evidence_response, "Huella completa del documento")

    def test_business_privacy_page_registers_a_client_rights_request(self):
        self.complete_legal_onboarding()
        self.client.logout()
        client_record = BusinessClient.objects.create(
            business=self.business,
            full_name="Ana Cliente",
            phone="600222333",
        )
        access = BusinessClientAccess(
            business=self.business,
            business_client=client_record,
            phone="600222333",
        )
        access.set_password("client-pass-123")
        access.save()
        session = self.client.session
        session[CLIENT_ACCESS_SESSION_KEY] = access.pk
        session[CLIENT_ACCESS_LAST_SEEN_SESSION_KEY] = timezone.now().isoformat()
        session.save()

        response = self.client.post(
            reverse("legal:business_privacy", args=[self.business.slug]),
            {
                "request_type": DataRightsRequest.RequestType.ACCESS,
                "detail": "Quiero conocer los datos vinculados a mi cuenta.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La solicitud queda registrada")
        rights_request = DataRightsRequest.objects.get(business=self.business)
        self.assertEqual(rights_request.client_access, access)
        self.assertEqual(rights_request.status, DataRightsRequest.Status.RECEIVED)

    def test_professional_can_update_a_request_from_the_privacy_center(self):
        self.complete_legal_onboarding()
        client_record = BusinessClient.objects.create(
            business=self.business,
            full_name="Ana Cliente",
            phone="600333444",
        )
        access = BusinessClientAccess(
            business=self.business,
            business_client=client_record,
            phone="600333444",
        )
        access.set_password("client-pass-123")
        access.save()
        rights_request = DataRightsRequest.objects.create(
            business=self.business,
            client_access=access,
            request_type=DataRightsRequest.RequestType.RECTIFICATION,
            detail="Mi nombre debe corregirse.",
        )

        response = self.client.post(
            reverse(
                "legal:professional_rights_request_update",
                args=[rights_request.pk],
            ),
            {
                "status": DataRightsRequest.Status.RESOLVED,
                "resolution_note": "Datos revisados con la clienta.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La solicitud de derechos queda actualizada")
        rights_request.refresh_from_db()
        self.assertEqual(rights_request.status, DataRightsRequest.Status.RESOLVED)
        self.assertIsNotNone(rights_request.resolved_at)

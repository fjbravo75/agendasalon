from datetime import timedelta
from urllib.parse import urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.customers.models import BusinessClient, BusinessClientAccess
from apps.customers.services import register_client_access
from apps.legal.models import (
    BusinessLegalProfile,
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    DataRightsRequest,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)
from apps.legal.presentations import (
    LEGAL_PRESENTATION_CHANGED_MESSAGE,
    LEGAL_PRESENTATION_TRANSACTION_REQUIRED_MESSAGE,
    LegalPresentationError,
    LegalPresentationScope,
    issue_legal_presentation,
    resolve_legal_presentation,
)
from apps.legal.services import (
    acknowledge_customer_privacy,
    business_legal_snapshot,
    customer_privacy_status,
    platform_legal_context,
    professional_legal_status,
    record_customer_privacy_information,
)
from apps.notifications.services import client_verification_url


class LegalPresentationTransactionContractTests(TransactionTestCase):
    def test_receipt_resolution_requires_an_open_atomic_transaction(self):
        self.assertFalse(transaction.get_connection().in_atomic_block)

        with self.assertRaisesMessage(
            transaction.TransactionManagementError,
            LEGAL_PRESENTATION_TRANSACTION_REQUIRED_MESSAGE,
        ):
            resolve_legal_presentation(
                "unused-without-transaction",
                scope=LegalPresentationScope.BUSINESS_SIGNUP,
                audience={"channel": "public"},
                required_kinds=(LegalDocument.Kind.PLATFORM_PRIVACY,),
                legal_context=platform_legal_context(),
            )


class LegalPresentationReceiptTests(TestCase):
    def setUp(self):
        self.document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.PLATFORM_PRIVACY,
            is_active=True,
        )
        self.scope = LegalPresentationScope.BUSINESS_SIGNUP
        self.audience = {"channel": "public"}
        self.context = platform_legal_context()

    def receipt_token(self):
        return issue_legal_presentation(
            scope=self.scope,
            audience=self.audience,
            documents=(self.document,),
            legal_context=self.context,
        )

    def resolve(self, token, *, audience=None):
        return resolve_legal_presentation(
            token,
            scope=self.scope,
            audience=audience or self.audience,
            required_kinds=(LegalDocument.Kind.PLATFORM_PRIVACY,),
            legal_context=self.context,
        )

    def test_receipt_resolves_the_exact_active_document(self):
        receipt = self.resolve(self.receipt_token())

        self.assertEqual(
            receipt.document(LegalDocument.Kind.PLATFORM_PRIVACY),
            self.document,
        )
        self.assertEqual(receipt.legal_context, self.context)
        self.assertTrue(receipt.receipt_id)

    def test_each_issued_receipt_has_a_distinct_action_identifier(self):
        first = self.resolve(self.receipt_token())
        second = self.resolve(self.receipt_token())

        self.assertNotEqual(first.receipt_id, second.receipt_id)

    def test_published_document_cannot_be_changed_or_deleted(self):
        token = self.receipt_token()
        original_title = self.document.title
        original_sections = self.document.sections
        original_hash = self.document.content_hash

        with self.assertRaisesMessage(TypeError, "Una versión publicada es inmutable"):
            LegalDocument.objects.filter(pk=self.document.pk).update(
                sections=[{"heading": "Contenido sustituido", "body": "No permitido"}]
            )

        self.document.title = "Título sustituido"
        with self.assertRaisesMessage(
            ValidationError,
            "Una versión publicada es inmutable",
        ):
            self.document.save()

        self.document.refresh_from_db()
        self.assertEqual(self.document.title, original_title)
        self.assertEqual(self.document.sections, original_sections)
        self.assertEqual(self.document.content_hash, original_hash)
        self.assertEqual(
            self.resolve(token).document(LegalDocument.Kind.PLATFORM_PRIVACY),
            self.document,
        )

        with self.assertRaisesMessage(TypeError, "Una versión publicada es inmutable"):
            LegalDocument.objects.filter(pk=self.document.pk).delete()
        with self.assertRaisesMessage(TypeError, "Una versión publicada es inmutable"):
            self.document.delete()

    def test_published_document_allows_only_controlled_active_state_changes(self):
        LegalDocument.objects.filter(pk=self.document.pk).update(is_active=False)
        self.document.refresh_from_db()
        self.assertFalse(self.document.is_active)

        self.document.is_active = True
        self.document.save(update_fields=["is_active"])
        self.document.refresh_from_db()
        self.assertTrue(self.document.is_active)

    def test_manipulated_receipt_is_rejected(self):
        token = self.receipt_token()
        manipulated = f"{token[:-1]}{'x' if token[-1] != 'x' else 'y'}"

        with self.assertRaisesMessage(
            LegalPresentationError,
            LEGAL_PRESENTATION_CHANGED_MESSAGE,
        ):
            self.resolve(manipulated)

    def test_expired_receipt_is_rejected_deterministically(self):
        token = self.receipt_token()

        with patch("apps.legal.presentations.LEGAL_PRESENTATION_MAX_AGE_SECONDS", -1):
            with self.assertRaisesMessage(
                LegalPresentationError,
                LEGAL_PRESENTATION_CHANGED_MESSAGE,
            ):
                self.resolve(token)

    def test_receipt_cannot_cross_audiences(self):
        token = self.receipt_token()

        with self.assertRaisesMessage(
            LegalPresentationError,
            LEGAL_PRESENTATION_CHANGED_MESSAGE,
        ):
            self.resolve(token, audience={"channel": "internal"})

    def test_missing_document_is_rejected_with_the_domain_error(self):
        with self.assertRaisesMessage(
            LegalPresentationError,
            LEGAL_PRESENTATION_CHANGED_MESSAGE,
        ):
            issue_legal_presentation(
                scope=self.scope,
                audience=self.audience,
                documents=(None,),
                legal_context=self.context,
            )


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
        page = self.client.get(reverse("legal:professional_onboarding"))
        return self.client.post(
            reverse("legal:professional_onboarding"),
            {
                **self.legal_onboarding_payload(),
                "legal_presentation_token": page.context["legal_presentation_token"],
            },
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
        expected_context = {
            "platform": platform_legal_context(),
            "business": profile.snapshot(),
        }
        self.assertTrue(
            all(
                item.legal_context_snapshot == expected_context
                for item in acceptances
            )
        )

    def test_professional_onboarding_rejects_a_document_rotated_after_get_without_writes(self):
        self.client.force_login(self.professional)
        page = self.client.get(reverse("legal:professional_onboarding"))
        previous_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.PLATFORM_PRIVACY,
            is_active=True,
        )
        previous_document.is_active = False
        previous_document.save(update_fields=["is_active"])
        replacement = LegalDocument.objects.create(
            kind=previous_document.kind,
            slug="privacidad-plataforma-presentacion-v2",
            version="presentacion-v2",
            title=previous_document.title,
            lead=previous_document.lead,
            sections=previous_document.sections,
            is_active=True,
        )

        response = self.client.post(
            reverse("legal:professional_onboarding"),
            {
                **self.legal_onboarding_payload(),
                "legal_presentation_token": page.context["legal_presentation_token"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, LEGAL_PRESENTATION_CHANGED_MESSAGE)
        self.assertContains(response, replacement.version)
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, 'data-error-summary')
        self.assertFalse(BusinessLegalProfile.objects.filter(business=self.business).exists())
        self.assertFalse(LegalAcceptance.objects.filter(business=self.business).exists())

    def test_onboarding_ordinary_error_preserves_current_confirmations(self):
        self.client.force_login(self.professional)
        expected_next = reverse("customers:professional_client_list")
        page = self.client.get(
            reverse("legal:professional_onboarding"),
            {"next": expected_next},
        )
        token = page.context["legal_presentation_token"]

        response = self.client.post(
            reverse("legal:professional_onboarding"),
            {
                **self.legal_onboarding_payload(),
                "privacy_email": "correo-no-valido",
                "next": expected_next,
                "legal_presentation_token": token,
            },
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["onboarding_form"]
        for field_name in (
            "platform_privacy_acknowledged",
            "terms_accepted",
            "data_processing_accepted",
            "authority_declared",
        ):
            self.assertTrue(form[field_name].value())
        self.assertEqual(response.context["legal_presentation_token"], token)
        self.assertEqual(response.context["next_url"], expected_next)
        self.assertContains(response, f'name="next" value="{expected_next}"')
        self.assertFalse(BusinessLegalProfile.objects.filter(business=self.business).exists())
        self.assertFalse(LegalAcceptance.objects.filter(business=self.business).exists())

    def test_onboarding_rejects_unsafe_next_destinations(self):
        self.client.force_login(self.professional)
        url = reverse("legal:professional_onboarding")
        unsafe_destinations = (
            "//evil.example/steal",
            r"/\evil.example/steal",
            r"\evil.example/steal",
            "https://evil.example/steal",
            "http://evil.example/steal",
        )

        for next_url in unsafe_destinations:
            with self.subTest(next_url=next_url):
                page = self.client.get(url, {"next": next_url})
                response = self.client.post(
                    url,
                    {
                        **self.legal_onboarding_payload(),
                        "next": next_url,
                        "legal_presentation_token": page.context[
                            "legal_presentation_token"
                        ],
                    },
                )
                self.assertRedirects(
                    response,
                    reverse("legal:professional_center"),
                    fetch_redirect_response=False,
                )
                self.assertEqual(page.context["next_url"], "")

    def test_onboarding_preserves_the_middleware_internal_next_destination(self):
        self.client.force_login(self.professional)
        url = reverse("legal:professional_onboarding")
        expected_next = reverse("customers:professional_client_list")
        blocked_response = self.client.get(expected_next)

        self.assertEqual(blocked_response.status_code, 302)
        page = self.client.get(blocked_response.url)

        self.assertEqual(page.context["next_url"], expected_next)
        response = self.client.post(
            url,
            {
                **self.legal_onboarding_payload(),
                "next": expected_next,
                "legal_presentation_token": page.context[
                    "legal_presentation_token"
                ],
            },
        )

        self.assertRedirects(
            response,
            expected_next,
            fetch_redirect_response=False,
        )

    def test_onboarding_missing_document_returns_503_without_writes(self):
        self.client.force_login(self.professional)
        LegalDocument.objects.filter(
            kind=LegalDocument.Kind.DATA_PROCESSING,
            is_active=True,
        ).update(is_active=False)
        url = reverse("legal:professional_onboarding")

        page = self.client.get(url)

        self.assertEqual(page.status_code, 503)
        self.assertContains(
            page,
            "Ahora mismo no podemos mostrar toda la documentación legal necesaria",
            status_code=503,
        )
        self.assertContains(page, "No hemos guardado ningún cambio", status_code=503)
        self.assertNotContains(page, "Guardar y activar privacidad", status_code=503)
        self.assertContains(
            page,
            '<fieldset class="legal-form__fields" disabled>',
            status_code=503,
        )
        self.assertEqual(page.context["legal_presentation_token"], "")

        response = self.client.post(
            url,
            {
                **self.legal_onboarding_payload(),
                "privacy_email": "correo-no-valido",
                "legal_presentation_token": "",
            },
        )
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "No hemos guardado ningún cambio", status_code=503)
        self.assertContains(
            response,
            '<fieldset class="legal-form__fields" disabled>',
            status_code=503,
        )
        self.assertFalse(BusinessLegalProfile.objects.filter(business=self.business).exists())
        self.assertFalse(LegalAcceptance.objects.filter(business=self.business).exists())

    def test_rotated_onboarding_with_another_error_clears_every_legal_confirmation(self):
        self.client.force_login(self.professional)
        page = self.client.get(reverse("legal:professional_onboarding"))
        old_token = page.context["legal_presentation_token"]
        old_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.TERMS,
            is_active=True,
        )
        LegalDocument.objects.filter(pk=old_document.pk).update(is_active=False)
        replacement = LegalDocument.objects.create(
            kind=old_document.kind,
            slug="condiciones-servicio-presentacion-b",
            version="terms-rotation-b",
            title=old_document.title,
            lead=old_document.lead,
            sections=old_document.sections,
            is_active=True,
        )

        response = self.client.post(
            reverse("legal:professional_onboarding"),
            {
                **self.legal_onboarding_payload(),
                "privacy_email": "correo-no-valido",
                "legal_presentation_token": old_token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, LEGAL_PRESENTATION_CHANGED_MESSAGE)
        self.assertContains(response, replacement.version)
        form = response.context["onboarding_form"]
        for field_name in (
            "platform_privacy_acknowledged",
            "terms_accepted",
            "data_processing_accepted",
            "authority_declared",
        ):
            self.assertFalse(form[field_name].value())
        self.assertNotEqual(response.context["legal_presentation_token"], old_token)
        self.assertFalse(BusinessLegalProfile.objects.filter(business=self.business).exists())
        self.assertFalse(LegalAcceptance.objects.filter(business=self.business).exists())

        corrected_payload = self.legal_onboarding_payload()
        for field_name in (
            "platform_privacy_acknowledged",
            "terms_accepted",
            "data_processing_accepted",
            "authority_declared",
        ):
            corrected_payload.pop(field_name)
        reconfirmation_required = self.client.post(
            reverse("legal:professional_onboarding"),
            {
                **corrected_payload,
                "legal_presentation_token": response.context[
                    "legal_presentation_token"
                ],
            },
        )
        self.assertEqual(reconfirmation_required.status_code, 200)
        self.assertFalse(BusinessLegalProfile.objects.filter(business=self.business).exists())
        self.assertFalse(LegalAcceptance.objects.filter(business=self.business).exists())

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

    def test_business_legal_status_requires_the_current_business_and_platform_context(self):
        self.complete_legal_onboarding()
        self.assertTrue(professional_legal_status(self.professional, self.business)["is_current"])

        profile = BusinessLegalProfile.objects.get(business=self.business)
        profile.privacy_email = "contexto-nuevo@example.com"
        profile.save(update_fields=["privacy_email", "updated_at"])
        self.assertFalse(
            professional_legal_status(self.professional, self.business)["is_current"]
        )

        profile.privacy_email = "privacidad@example.com"
        profile.save(update_fields=["privacy_email", "updated_at"])
        with self.settings(AGENDA_PLATFORM_LEGAL_NAME="AgendaSalon con nueva identidad"):
            self.assertFalse(
                professional_legal_status(self.professional, self.business)[
                    "is_current"
                ]
            )

    def test_legacy_business_only_acceptance_context_remains_read_compatible(self):
        self.complete_legal_onboarding()
        profile = BusinessLegalProfile.objects.get(business=self.business)
        LegalAcceptance.objects.filter(
            business=self.business,
            actor_user=self.professional,
        ).update(legal_context_snapshot=profile.snapshot())

        status = professional_legal_status(self.professional, self.business)

        self.assertTrue(status["is_current"])
        self.assertEqual(status["label"], "Documentación vigente")

    def test_business_legal_status_rejects_a_changed_acceptance_hash(self):
        self.complete_legal_onboarding()
        document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.TERMS,
            is_active=True,
        )
        LegalAcceptance.objects.filter(
            business=self.business,
            document=document,
        ).update(document_hash_snapshot="f" * 64)

        status = professional_legal_status(self.professional, self.business)

        self.assertFalse(status["is_current"])
        terms_row = next(
            row
            for row in status["document_rows"]
            if row["kind"] == LegalDocument.Kind.TERMS
        )
        self.assertFalse(terms_row["is_current"])

    def test_reaccepting_the_same_documents_renews_timestamp_and_context(self):
        self.complete_legal_onboarding()
        acceptance = LegalAcceptance.objects.get(
            business=self.business,
            actor_user=self.professional,
            document__kind=LegalDocument.Kind.TERMS,
        )
        renewed_at = acceptance.accepted_at + timedelta(minutes=5)
        original_terms_event = LegalAcceptanceEvent.objects.get(
            business=self.business,
            actor_user=self.professional,
            document__kind=LegalDocument.Kind.TERMS,
        )
        page = self.client.get(reverse("legal:professional_onboarding"))

        with patch("apps.legal.services.timezone.now", return_value=renewed_at):
            response = self.client.post(
                reverse("legal:professional_onboarding"),
                {
                    **self.legal_onboarding_payload(),
                    "privacy_email": "privacidad-renovada@example.com",
                    "legal_presentation_token": page.context[
                        "legal_presentation_token"
                    ],
                },
            )

        self.assertEqual(response.status_code, 302)
        acceptance.refresh_from_db()
        self.assertEqual(acceptance.accepted_at, renewed_at)
        self.assertEqual(
            acceptance.legal_context_snapshot["business"]["privacy_email"],
            "privacidad-renovada@example.com",
        )
        self.assertEqual(
            LegalAcceptance.objects.filter(
                business=self.business,
                actor_user=self.professional,
            ).count(),
            3,
        )
        terms_events = LegalAcceptanceEvent.objects.filter(
            business=self.business,
            actor_user=self.professional,
            document__kind=LegalDocument.Kind.TERMS,
        )
        self.assertEqual(terms_events.count(), 2)
        self.assertTrue(terms_events.filter(pk=original_terms_event.pk).exists())
        self.assertTrue(
            terms_events.filter(
                legal_context_snapshot__business__privacy_email=(
                    "privacidad-renovada@example.com"
                )
            ).exists()
        )
        self.assertEqual(
            LegalAcceptanceEvent.objects.filter(
                business=self.business,
                actor_user=self.professional,
            ).count(),
            6,
        )

        superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34600999007",
            phone="+34600999007",
            password="test-pass-123",
            full_name="Superadministración Historial",
        )
        self.client.force_login(superadmin)
        history_response = self.client.get(
            reverse(
                "businesses:superadmin_business_legal_evidence",
                args=[self.business.pk],
            )
        )
        visible_terms_events = tuple(
            event
            for event in history_response.context["acceptance_history"]
            if event.document.kind == LegalDocument.Kind.TERMS
        )
        self.assertEqual(len(visible_terms_events), 2)

    def test_repeating_the_same_onboarding_receipt_is_idempotent(self):
        self.client.force_login(self.professional)
        page = self.client.get(reverse("legal:professional_onboarding"))
        payload = {
            **self.legal_onboarding_payload(),
            "legal_presentation_token": page.context["legal_presentation_token"],
        }

        first = self.client.post(reverse("legal:professional_onboarding"), payload)
        first_event_ids = tuple(
            LegalAcceptanceEvent.objects.filter(business=self.business)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        first_timestamps = {
            event.document_id: event.accepted_at
            for event in LegalAcceptanceEvent.objects.filter(business=self.business)
        }
        self.assertTrue(
            all(
                acceptance.accepted_at
                == first_timestamps[acceptance.document_id]
                for acceptance in LegalAcceptance.objects.filter(business=self.business)
            )
        )
        second = self.client.post(reverse("legal:professional_onboarding"), payload)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(len(first_event_ids), 3)
        self.assertEqual(
            tuple(
                LegalAcceptanceEvent.objects.filter(business=self.business)
                .order_by("pk")
                .values_list("pk", flat=True)
            ),
            first_event_ids,
        )
        self.assertEqual(LegalAcceptance.objects.filter(business=self.business).count(), 3)
        self.assertEqual(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=(
                    BusinessActivityEvent.EventType.LEGAL_DOCUMENTATION_ACCEPTED
                ),
            ).count(),
            1,
        )
        self.assertTrue(
            all(
                acceptance.accepted_at
                == first_timestamps[acceptance.document_id]
                for acceptance in LegalAcceptance.objects.filter(business=self.business)
            )
        )

    def test_onboarding_receipt_cannot_be_reused_with_another_profile(self):
        self.client.force_login(self.professional)
        url = reverse("legal:professional_onboarding")
        page = self.client.get(url)
        token = page.context["legal_presentation_token"]
        original_payload = {
            **self.legal_onboarding_payload(),
            "legal_presentation_token": token,
        }

        first = self.client.post(url, original_payload)
        altered = self.client.post(
            url,
            {
                **original_payload,
                "privacy_email": "otro-contacto@example.com",
            },
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(altered.status_code, 200)
        self.assertContains(
            altered,
            "No podemos reutilizar esta confirmación con otros datos",
        )
        profile = BusinessLegalProfile.objects.get(business=self.business)
        self.assertEqual(profile.privacy_email, "privacidad@example.com")
        self.assertEqual(
            LegalAcceptanceEvent.objects.filter(business=self.business).count(),
            3,
        )
        self.assertEqual(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=(
                    BusinessActivityEvent.EventType.LEGAL_DOCUMENTATION_ACCEPTED
                ),
            ).count(),
            1,
        )
        for field_name in (
            "platform_privacy_acknowledged",
            "terms_accepted",
            "data_processing_accepted",
            "authority_declared",
        ):
            self.assertFalse(altered.context["onboarding_form"][field_name].value())
        self.assertNotEqual(altered.context["legal_presentation_token"], token)

    def test_customer_verification_records_versioned_privacy_evidence(self):
        self.complete_legal_onboarding()
        self.client.logout()
        test_password = "".join(("Una", "-clave-", "segura-", "2026"))

        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "full_name": "Ana Cliente",
                "phone": "600 555 111",
                "email": "ana.cliente@example.com",
            },
        )

        self.assertEqual(response.status_code, 302)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="ana.cliente@example.com",
        )
        self.assertFalse(
            CustomerPrivacyEvidence.objects.filter(client_access=access).exists()
        )

        verify_path = urlparse(client_verification_url(access)).path
        verification_page = self.client.get(verify_path)
        response = self.client.post(
            verify_path,
            {
                "password": test_password,
                "password_confirm": test_password,
                "privacy_acknowledged": "on",
                "legal_presentation_token": verification_page.context[
                    "legal_presentation_token"
                ],
            },
        )

        self.assertEqual(response.status_code, 302)
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

        profile = BusinessLegalProfile.objects.get(business=self.business)
        profile.privacy_email = "privacidad-renovada@example.com"
        profile.save(update_fields=["privacy_email", "updated_at"])
        self.assertFalse(customer_privacy_status(access.business_client)["is_current"])

        acknowledge_customer_privacy(
            client_access=access,
            context=LegalAcceptance.Context.CLIENT_REGISTRATION,
            document=evidence.document,
            legal_context_snapshot=business_legal_snapshot(self.business),
        )
        renewed = CustomerPrivacyEvidence.objects.get(pk=evidence.pk)
        self.assertEqual(
            renewed.legal_context_snapshot,
            business_legal_snapshot(self.business),
        )
        self.assertTrue(customer_privacy_status(access.business_client)["is_current"])
        privacy_events = CustomerPrivacyEvidenceEvent.objects.filter(
            business_client=access.business_client,
        )
        self.assertEqual(privacy_events.count(), 2)
        self.assertEqual(
            privacy_events.order_by("occurred_at", "pk").first().legal_context_snapshot[
                "privacy_email"
            ],
            "privacidad@example.com",
        )
        self.assertEqual(
            privacy_events.order_by("-occurred_at", "-pk").first().legal_context_snapshot[
                "privacy_email"
            ],
            "privacidad-renovada@example.com",
        )

    def test_repeating_the_same_customer_action_key_is_idempotent(self):
        self.complete_legal_onboarding()
        access = register_client_access(
            business=self.business,
            full_name="Cliente idempotente",
            phone="600222334",
            email="cliente.idempotente@example.com",
            password="client-pass-123",
            email_verified=True,
        )
        document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        kwargs = {
            "client_access": access,
            "context": LegalAcceptance.Context.CLIENT_REGISTRATION,
            "document": document,
            "legal_context_snapshot": business_legal_snapshot(self.business),
            "action_fingerprint_source": "receipt-idempotente-1",
        }

        first = acknowledge_customer_privacy(**kwargs)
        second = acknowledge_customer_privacy(**kwargs)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(client_access=access).count(),
            1,
        )
        self.assertEqual(
            LegalAcceptanceEvent.objects.filter(client_access=access).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(client_access=access).count(),
            1,
        )

    def test_professional_can_record_in_person_privacy_information(self):
        self.complete_legal_onboarding()
        client_record = BusinessClient.objects.create(
            business=self.business,
            full_name="Ana Cliente",
            phone="600777888",
        )

        detail_page = self.client.get(
            reverse("customers:professional_client_detail", args=[client_record.pk])
        )
        response = self.client.post(
            reverse(
                "customers:professional_client_privacy_record",
                args=[client_record.pk],
            ),
            {
                "channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "legal_presentation_token": detail_page.context[
                    "legal_presentation_token"
                ],
            },
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

    def test_legal_event_books_reject_every_mutation_path(self):
        self.complete_legal_onboarding()
        client_record = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente con historial protegido",
            phone="600777887",
        )
        privacy_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        record_customer_privacy_information(
            business_client=client_record,
            recorded_by=self.professional,
            channel=CustomerPrivacyEvidence.Channel.IN_PERSON,
            document=privacy_document,
            legal_context_snapshot=business_legal_snapshot(self.business),
            action_fingerprint_source="test:immutable-customer-event",
        )
        events = (
            LegalAcceptanceEvent.objects.filter(business=self.business).first(),
            CustomerPrivacyEvidenceEvent.objects.get(business=self.business),
        )

        for event in events:
            with self.subTest(model=event._meta.label):
                with self.assertRaisesMessage(
                    ValidationError,
                    "Las constancias históricas no se pueden modificar ni borrar",
                ):
                    event.save()
                with self.assertRaisesMessage(
                    TypeError,
                    "Las constancias históricas no se pueden modificar ni borrar",
                ):
                    event.__class__.objects.filter(pk=event.pk).update(
                        legal_context_snapshot={"alterado": True}
                    )
                with self.assertRaisesMessage(
                    TypeError,
                    "Las constancias históricas no se pueden modificar ni borrar",
                ):
                    event.__class__.objects.bulk_update(
                        [event],
                        ["legal_context_snapshot"],
                    )
                with self.assertRaisesMessage(
                    TypeError,
                    "Las constancias históricas no se pueden modificar ni borrar",
                ):
                    event.__class__.objects.bulk_create(
                        [event],
                        update_conflicts=True,
                        update_fields=["legal_context_snapshot"],
                        unique_fields=["pk"],
                    )
                with self.assertRaisesMessage(
                    TypeError,
                    "Las constancias históricas no se pueden modificar ni borrar",
                ):
                    event.__class__.objects.filter(pk=event.pk).delete()
                with self.assertRaisesMessage(
                    TypeError,
                    "Las constancias históricas no se pueden modificar ni borrar",
                ):
                    event.delete()
                duplicate = event.__class__(
                    **{
                        field.attname: getattr(event, field.attname)
                        for field in event._meta.concrete_fields
                        if not field.auto_created or field.primary_key
                    }
                )
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        duplicate.save()

    def test_customer_privacy_becomes_pending_when_the_business_context_changes(self):
        self.complete_legal_onboarding()
        client_record = BusinessClient.objects.create(
            business=self.business,
            full_name="Ana Cliente",
            phone="600777889",
        )
        detail_page = self.client.get(
            reverse("customers:professional_client_detail", args=[client_record.pk])
        )
        self.client.post(
            reverse(
                "customers:professional_client_privacy_record",
                args=[client_record.pk],
            ),
            {
                "channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "legal_presentation_token": detail_page.context[
                    "legal_presentation_token"
                ],
            },
        )
        evidence = CustomerPrivacyEvidence.objects.get(business_client=client_record)
        self.assertTrue(customer_privacy_status(client_record)["is_current"])

        profile = BusinessLegalProfile.objects.get(business=self.business)
        profile.privacy_email = "nuevo-contacto@example.com"
        profile.save(update_fields=["privacy_email", "updated_at"])

        status = customer_privacy_status(client_record)
        self.assertFalse(status["is_current"])
        self.assertEqual(status["label"], "Información pendiente")
        self.assertTrue(
            any(
                event.document_id == evidence.document_id
                and event.document_hash_snapshot == evidence.document_hash_snapshot
                and event.legal_context_snapshot == evidence.legal_context_snapshot
                for event in status["history"]
            )
        )

    def test_customer_privacy_status_rejects_a_changed_document_hash(self):
        self.complete_legal_onboarding()
        client_record = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente con huella alterada",
            phone="600777890",
        )
        detail_page = self.client.get(
            reverse("customers:professional_client_detail", args=[client_record.pk])
        )
        self.client.post(
            reverse(
                "customers:professional_client_privacy_record",
                args=[client_record.pk],
            ),
            {
                "channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "legal_presentation_token": detail_page.context[
                    "legal_presentation_token"
                ],
            },
        )
        evidence = CustomerPrivacyEvidence.objects.get(business_client=client_record)
        CustomerPrivacyEvidence.objects.filter(pk=evidence.pk).update(
            document_hash_snapshot="e" * 64
        )

        status = customer_privacy_status(client_record)

        self.assertFalse(status["is_current"])
        self.assertEqual(status["label"], "Información pendiente")

    def test_customer_privacy_history_survives_disabled_or_missing_current_control(self):
        self.complete_legal_onboarding()
        client_record = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente con historial conservado",
            phone="600777891",
        )
        detail_page = self.client.get(
            reverse("customers:professional_client_detail", args=[client_record.pk])
        )
        self.client.post(
            reverse(
                "customers:professional_client_privacy_record",
                args=[client_record.pk],
            ),
            {
                "channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "legal_presentation_token": detail_page.context[
                    "legal_presentation_token"
                ],
            },
        )
        event = CustomerPrivacyEvidenceEvent.objects.get(
            business_client=client_record
        )

        Business.objects.filter(pk=self.business.pk).update(
            legal_compliance_enabled=False
        )
        client_record.business.refresh_from_db()
        disabled_status = customer_privacy_status(client_record)

        self.assertEqual(disabled_status["latest_evidence"], event)
        self.assertEqual(disabled_status["history"], (event,))

        Business.objects.filter(pk=self.business.pk).update(
            legal_compliance_enabled=True
        )
        client_record.business.refresh_from_db()
        missing_status = customer_privacy_status(client_record, document=None)

        self.assertEqual(missing_status["label"], "Política no disponible")
        self.assertEqual(missing_status["latest_evidence"], event)
        self.assertEqual(missing_status["history"], (event,))

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
        self.assertContains(evidence_response, "Huella digital completa del documento")
        self.assertContains(evidence_response, "Historial protegido")
        self.assertContains(
            evidence_response,
            "Estas constancias no se pueden modificar ni borrar desde la aplicación",
        )

    def test_business_privacy_page_registers_a_client_rights_request(self):
        self.complete_legal_onboarding()
        self.client.logout()
        access = register_client_access(
            business=self.business,
            full_name="Ana Cliente",
            phone="600222333",
            email="ana.derechos@example.com",
            password="client-pass-123",
            email_verified=True,
        )
        login_response = self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {
                "identifier": access.email,
                "password": "client-pass-123",
            },
        )
        self.assertEqual(login_response.status_code, 302)

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

    def test_rights_remain_available_without_a_current_privacy_document(self):
        self.complete_legal_onboarding()
        self.client.logout()
        access = register_client_access(
            business=self.business,
            full_name="Cliente sin texto vigente",
            phone="600222334",
            email="derechos.sin.texto@example.com",
            password="client-pass-123",
            email_verified=True,
        )
        login_response = self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {
                "identifier": access.email,
                "password": "client-pass-123",
            },
        )
        self.assertEqual(login_response.status_code, 302)
        LegalDocument.objects.filter(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        ).update(is_active=False)
        url = reverse("legal:business_privacy", args=[self.business.slug])

        page = self.client.get(url)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "El canal de contacto y el ejercicio de tus derechos")
        self.assertContains(page, "Tus derechos siguen activos")
        self.assertContains(page, "Registrar solicitud")

        response = self.client.post(
            url,
            {
                "request_type": DataRightsRequest.RequestType.ERASURE,
                "detail": "Quiero solicitar la supresión de mis datos.",
            },
        )

        self.assertEqual(response.status_code, 302)
        rights_request = DataRightsRequest.objects.get(
            business=self.business,
            client_access=access,
        )
        self.assertEqual(
            rights_request.request_type,
            DataRightsRequest.RequestType.ERASURE,
        )

    def test_business_privacy_remains_visible_when_the_business_is_paused(self):
        self.complete_legal_onboarding()
        self.business.is_active = False
        self.business.public_booking_enabled = False
        self.business.save(update_fields=["is_active", "public_booking_enabled", "updated_at"])

        response = self.client.get(
            reverse("legal:business_privacy", args=[self.business.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacidad")

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

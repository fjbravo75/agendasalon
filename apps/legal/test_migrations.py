from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.businesses.models import Business
from apps.customers.models import BusinessClient
from apps.legal.models import (
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)


class LegalEvidenceBackfillTests(TestCase):
    def test_backfill_copies_each_projection_with_its_exact_snapshots(self):
        professional = get_user_model().objects.create_user(
            normalized_phone="+34600111701",
            phone="+34600111701",
            password="test-pass-123",
            full_name="Profesional Backfill",
        )
        business = Business.objects.create(
            commercial_name="Negocio Backfill",
            slug="negocio-backfill-legal",
        )
        terms = LegalDocument.objects.get(
            kind=LegalDocument.Kind.TERMS,
            is_active=True,
        )
        privacy = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        accepted_at = timezone.now()
        acceptance = LegalAcceptance.objects.create(
            document=terms,
            business=business,
            actor_user=professional,
            action=LegalAcceptance.Action.ACCEPTED,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
            document_hash_snapshot=terms.content_hash,
            legal_context_snapshot={"snapshot": "aceptación exacta"},
            authority_declared=True,
            accepted_at=accepted_at,
        )
        client = BusinessClient.objects.create(
            business=business,
            full_name="Cliente Backfill",
            phone="600117701",
        )
        occurred_at = timezone.now()
        evidence = CustomerPrivacyEvidence.objects.create(
            document=privacy,
            business=business,
            business_client=client,
            recorded_by=professional,
            event_type=CustomerPrivacyEvidence.EventType.INFORMATION_PROVIDED,
            channel=CustomerPrivacyEvidence.Channel.IN_PERSON,
            informed_party_type=CustomerPrivacyEvidence.InformedParty.CLIENT,
            informed_party_name_snapshot=client.full_name,
            document_hash_snapshot=privacy.content_hash,
            legal_context_snapshot={"snapshot": "privacidad exacta"},
            occurred_at=occurred_at,
        )

        migration = import_module(
            "apps.legal.migrations.0007_legalacceptanceevent"
        )
        migration.backfill_legal_evidence_events(django_apps, None)

        self.assertEqual(LegalAcceptanceEvent.objects.count(), 1)
        acceptance_event = LegalAcceptanceEvent.objects.get()
        self.assertEqual(acceptance_event.document_id, acceptance.document_id)
        self.assertEqual(acceptance_event.business_id, acceptance.business_id)
        self.assertEqual(acceptance_event.actor_user_id, acceptance.actor_user_id)
        self.assertEqual(acceptance_event.action, acceptance.action)
        self.assertEqual(acceptance_event.context, acceptance.context)
        self.assertEqual(
            acceptance_event.document_hash_snapshot,
            acceptance.document_hash_snapshot,
        )
        self.assertEqual(
            acceptance_event.legal_context_snapshot,
            acceptance.legal_context_snapshot,
        )
        self.assertEqual(acceptance_event.accepted_at, acceptance.accepted_at)
        self.assertEqual(acceptance_event.recorded_at, acceptance.accepted_at)

        self.assertEqual(CustomerPrivacyEvidenceEvent.objects.count(), 1)
        privacy_event = CustomerPrivacyEvidenceEvent.objects.get()
        self.assertEqual(privacy_event.document_id, evidence.document_id)
        self.assertEqual(privacy_event.business_id, evidence.business_id)
        self.assertEqual(privacy_event.business_client_id, evidence.business_client_id)
        self.assertEqual(privacy_event.recorded_by_id, evidence.recorded_by_id)
        self.assertEqual(privacy_event.event_type, evidence.event_type)
        self.assertEqual(privacy_event.channel, evidence.channel)
        self.assertEqual(
            privacy_event.informed_party_name_snapshot,
            evidence.informed_party_name_snapshot,
        )
        self.assertEqual(
            privacy_event.document_hash_snapshot,
            evidence.document_hash_snapshot,
        )
        self.assertEqual(
            privacy_event.legal_context_snapshot,
            evidence.legal_context_snapshot,
        )
        self.assertEqual(privacy_event.occurred_at, evidence.occurred_at)
        self.assertEqual(privacy_event.recorded_at, evidence.created_at)

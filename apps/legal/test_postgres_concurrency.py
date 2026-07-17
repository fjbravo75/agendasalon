from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TransactionTestCase

from apps.businesses.models import Business
from apps.customers.models import BusinessClient, BusinessClientAccess
from apps.legal.models import (
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)
from apps.legal.services import acknowledge_customer_privacy


@skipUnless(
    connection.vendor == "postgresql",
    "Esta prueba de concurrencia requiere PostgreSQL real.",
)
class PostgreSQLLegalEvidenceConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Salón legal concurrente",
            slug="salon-legal-concurrente",
            is_active=True,
            legal_compliance_enabled=True,
        )
        self.business_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente legal concurrente",
            phone="600123456",
        )
        self.client_access = BusinessClientAccess(
            business=self.business,
            business_client=self.business_client,
            phone="600123456",
            email="cliente.concurrente@example.test",
        )
        self.client_access.set_password("test-pass-123")
        self.client_access.full_clean()
        self.client_access.save()
        self.document = LegalDocument.objects.filter(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        ).first()
        if self.document is None:
            self.document = LegalDocument.objects.create(
                kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
                slug="privacidad-clientes-concurrencia-p1",
                version="p1-postgresql",
                title="Privacidad de clientes",
                lead="Información de privacidad para la prueba PostgreSQL.",
                sections=[
                    {
                        "heading": "Responsable",
                        "body": "El negocio informa sobre el tratamiento de datos.",
                    }
                ],
                is_active=True,
            )
        self.legal_context_snapshot = {
            "legal_name": "Salón legal concurrente, S.L.",
            "tax_identifier": "TEST-P1-LEGAL",
            "registered_address": "Entorno efímero PostgreSQL",
            "privacy_email": "privacidad@example.test",
        }

    def test_same_action_fingerprint_is_idempotent_across_two_transactions(self):
        start_barrier = Barrier(2, timeout=5)

        def acknowledge_in_own_connection(_worker):
            connections.close_all()
            try:
                access = BusinessClientAccess.objects.select_related(
                    "business",
                    "business_client",
                ).get(pk=self.client_access.pk)
                document = LegalDocument.objects.get(pk=self.document.pk)
                start_barrier.wait()
                evidence = acknowledge_customer_privacy(
                    client_access=access,
                    context=LegalAcceptance.Context.CLIENT_REGISTRATION,
                    document=document,
                    legal_context_snapshot=self.legal_context_snapshot,
                    action_fingerprint_source="p1-receipt-concurrente-unico",
                )
                return evidence.pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            evidence_ids = list(executor.map(acknowledge_in_own_connection, range(2)))

        acceptance_event = LegalAcceptanceEvent.objects.get(
            client_access=self.client_access,
        )
        privacy_event = CustomerPrivacyEvidenceEvent.objects.get(
            client_access=self.client_access,
        )
        acceptance = LegalAcceptance.objects.get(client_access=self.client_access)
        evidence = CustomerPrivacyEvidence.objects.get(client_access=self.client_access)

        self.assertEqual(evidence_ids, [evidence.pk, evidence.pk])
        self.assertEqual(
            LegalAcceptanceEvent.objects.filter(client_access=self.client_access).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(
                client_access=self.client_access,
            ).count(),
            1,
        )
        self.assertEqual(
            LegalAcceptance.objects.filter(client_access=self.client_access).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(
                client_access=self.client_access,
            ).count(),
            1,
        )
        self.assertEqual(acceptance_event.accepted_at, privacy_event.occurred_at)
        self.assertEqual(acceptance.accepted_at, acceptance_event.accepted_at)
        self.assertEqual(evidence.occurred_at, acceptance_event.accepted_at)

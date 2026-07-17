from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.businesses.models import Business
from apps.legal.models import LegalAcceptance, LegalAcceptanceEvent, LegalDocument


class LegalEvidenceEventMigrationExecutorTests(TransactionTestCase):
    migrate_from = ("legal", "0006_legal_notice_2026_07_3")

    @staticmethod
    def _targets_with_legal(executor, legal_target):
        return [
            legal_target if target[0] == "legal" else target
            for target in executor.loader.graph.leaf_nodes()
        ]

    @staticmethod
    def _ensure_active_document(*, kind, slug, title):
        document = LegalDocument.objects.filter(kind=kind, is_active=True).first()
        if document is not None:
            return document
        return LegalDocument.objects.create(
            kind=kind,
            slug=slug,
            version="test.1",
            title=title,
            lead="Documento autocontenido para la prueba de migración.",
            sections=[
                {
                    "heading": "Contenido de prueba",
                    "paragraphs": ["Texto de prueba."],
                }
            ],
            is_active=True,
        )

    def test_reverse_is_blocked_before_dropping_event_tables(self):
        document = self._ensure_active_document(
            kind=LegalDocument.Kind.TERMS,
            slug="test-terms",
            title="Condiciones de prueba",
        )
        self._ensure_active_document(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            slug="test-customer-privacy",
            title="Privacidad de clientes de prueba",
        )
        user = get_user_model().objects.create_user(
            normalized_phone="+34600111791",
            phone="+34600111791",
            password="test-pass-123",
            full_name="Profesional evento irreversible",
        )
        business = Business.objects.create(
            commercial_name="Negocio evento irreversible",
            slug="negocio-evento-irreversible",
        )
        event = LegalAcceptanceEvent.objects.create(
            document=document,
            business=business,
            actor_user=user,
            action=LegalAcceptance.Action.ACCEPTED,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
            document_hash_snapshot=document.content_hash,
            legal_context_snapshot={"source": "post-deploy-event"},
            authority_declared=True,
            action_fingerprint="f" * 64,
        )
        executor = MigrationExecutor(connection)
        migration = executor.loader.get_migration(
            "legal",
            "0007_legalacceptanceevent",
        )

        self.assertFalse(migration.operations[-1].reversible)
        with self.assertRaises(IrreversibleError):
            executor.migrate(self._targets_with_legal(executor, self.migrate_from))

        self.assertTrue(LegalAcceptanceEvent.objects.filter(pk=event.pk).exists())
        table_names = set(connection.introspection.table_names())
        self.assertIn("legal_legalacceptanceevent", table_names)
        self.assertIn("legal_customerprivacyevidenceevent", table_names)

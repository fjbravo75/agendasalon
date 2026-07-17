from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class OutboundEmailLeaseMigrationTests(TransactionTestCase):
    migrate_from = ("notifications", "0003_alter_outboundemail_kind")
    migrate_to = ("notifications", "0004_outboundemail_delivery_lease")

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_latest_migrations)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        self.email_ids = self._create_legacy_emails(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.migrated_apps = executor.loader.project_state([self.migrate_to]).apps

    def _restore_latest_migrations(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    @staticmethod
    def _create_legacy_emails(apps):
        outbound_email = apps.get_model("notifications", "OutboundEmail")
        common = {
            "kind": "professional_activation",
            "recipient_email": "migration@example.test",
            "scheduled_for": timezone.now(),
        }
        pending = outbound_email.objects.create(
            **common,
            status="pending",
            deduplication_key="migration-pending",
        )
        sent = outbound_email.objects.create(
            **common,
            status="sent",
            deduplication_key="migration-sent",
            sent_at=timezone.now(),
        )
        processing = outbound_email.objects.create(
            **common,
            status="processing",
            deduplication_key="migration-processing",
            attempts=1,
        )
        return {
            "pending": pending.pk,
            "sent": sent.pk,
            "processing": processing.pk,
        }

    def test_existing_states_are_preserved_and_processing_becomes_recoverable(self):
        outbound_email = self.migrated_apps.get_model("notifications", "OutboundEmail")
        emails = {
            name: outbound_email.objects.get(pk=email_id)
            for name, email_id in self.email_ids.items()
        }

        self.assertEqual(emails["pending"].status, "pending")
        self.assertIsNone(emails["pending"].lease_token)
        self.assertIsNone(emails["pending"].lease_expires_at)
        self.assertIsNone(emails["pending"].cancellation_requested_at)
        self.assertEqual(emails["sent"].status, "sent")
        self.assertIsNotNone(emails["sent"].sent_at)
        self.assertIsNone(emails["sent"].lease_token)
        self.assertIsNone(emails["sent"].lease_expires_at)
        self.assertIsNone(emails["sent"].cancellation_requested_at)

        processing = emails["processing"]
        self.assertEqual(processing.status, "processing")
        self.assertEqual(processing.attempts, 1)
        self.assertIsNotNone(processing.lease_token)
        self.assertLess(processing.lease_expires_at, timezone.now())
        self.assertIsNone(processing.cancellation_requested_at)

        references = {email.delivery_reference for email in emails.values()}
        self.assertNotIn(None, references)
        self.assertEqual(len(references), 3)

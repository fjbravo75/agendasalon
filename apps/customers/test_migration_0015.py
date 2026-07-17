from copy import deepcopy
from datetime import timedelta

from django.contrib.auth.hashers import make_password
from django.db import connection, connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class PendingPublicRegistrationExpiryMigrationTests(TransactionTestCase):
    database_alias = "customers_expiry_migration_test"
    migrate_from = ("customers", "0014_sync_business_client_email_from_access")
    migrate_to = (
        "customers",
        "0015_businessclientaccess_public_registration_expires_at",
    )

    def setUp(self):
        super().setUp()
        self.migration_connection = self._create_isolated_connection()
        self.addCleanup(self._close_isolated_connection)

        executor = MigrationExecutor(self.migration_connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        self.fixture = self._create_fixture(old_apps)

        executor = MigrationExecutor(self.migration_connection)
        executor.migrate([self.migrate_to])
        self.migrated_apps = executor.loader.project_state([self.migrate_to]).apps

    def _create_isolated_connection(self):
        settings_dict = deepcopy(connection.settings_dict)
        settings_dict.update(
            {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
                "USER": "",
                "PASSWORD": "",
                "HOST": "",
                "PORT": "",
                "OPTIONS": {},
            }
        )
        migration_connection = DatabaseWrapper(settings_dict, self.database_alias)
        migration_connection.ensure_connection()
        connections.databases[self.database_alias] = settings_dict
        connections[self.database_alias] = migration_connection
        return migration_connection

    def _close_isolated_connection(self):
        self.migration_connection.close()
        del connections[self.database_alias]
        connections.databases.pop(self.database_alias, None)

    def _create_fixture(self, apps):
        Business = apps.get_model("businesses", "Business")
        BusinessClient = apps.get_model("customers", "BusinessClient")
        BusinessClientAccess = apps.get_model("customers", "BusinessClientAccess")
        alias = self.database_alias
        business = Business.objects.using(alias).create(
            commercial_name="Negocio expiry",
            slug="negocio-expiry",
        )

        def create_access(suffix, *, pending):
            phone = f"6008810{suffix}"
            client = BusinessClient.objects.using(alias).create(
                business=business,
                full_name=f"Cliente {suffix}",
                full_name_normalized=f"cliente {suffix}",
                phone=phone,
                phone_normalized=phone,
                email=f"cliente-{suffix}@example.test",
                source="other",
                is_active=not pending,
            )
            access = BusinessClientAccess.objects.using(alias).create(
                business=business,
                business_client=client,
                phone=phone,
                phone_normalized=phone,
                email=f"cliente-{suffix}@example.test",
                email_normalized=f"cliente-{suffix}@example.test",
                password_hash=make_password(None),
                is_active=False if pending else True,
                is_pending_public_registration=pending,
            )
            return access

        pending = create_access("01", pending=True)
        regular = create_access("02", pending=False)
        created_at = timezone.now().replace(microsecond=0) - timedelta(days=3)
        BusinessClientAccess.objects.using(alias).filter(pk=pending.pk).update(
            created_at=created_at
        )
        return pending.pk, regular.pk, created_at

    def test_backfill_sets_expiry_only_for_pending_unverified_accesses(self):
        BusinessClientAccess = self.migrated_apps.get_model(
            "customers",
            "BusinessClientAccess",
        )
        pending_id, regular_id, created_at = self.fixture
        pending = BusinessClientAccess.objects.using(self.database_alias).get(pk=pending_id)
        regular = BusinessClientAccess.objects.using(self.database_alias).get(pk=regular_id)

        self.assertEqual(
            pending.public_registration_expires_at,
            created_at + timedelta(hours=48),
        )
        self.assertIsNone(regular.public_registration_expires_at)

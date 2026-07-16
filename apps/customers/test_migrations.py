from django.contrib.auth.hashers import is_password_usable, make_password
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class PendingPublicRegistrationMigrationTests(TransactionTestCase):
    migrate_from = (
        "customers",
        "0012_limit_client_identity_to_professional_source",
    )
    migrate_to = (
        "customers",
        "0013_businessclientaccess_pending_public_registration",
    )

    def setUp(self):
        super().setUp()
        self.addCleanup(self._restore_latest_migrations)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        self.fixture_ids = self._create_legacy_fixture(old_apps)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        self.migrated_apps = executor.loader.project_state([self.migrate_to]).apps

    def _restore_latest_migrations(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _create_legacy_fixture(self, apps):
        Business = apps.get_model("businesses", "Business")
        BusinessClient = apps.get_model("customers", "BusinessClient")
        BusinessClientAccess = apps.get_model("customers", "BusinessClientAccess")

        business = Business.objects.create(
            commercial_name="Negocio de prueba de migración",
            slug="negocio-prueba-migracion-pendiente",
        )

        def create_access(*, suffix, source, access_active=True, verified=False):
            phone = f"6000000{suffix}"
            client = BusinessClient.objects.create(
                business=business,
                full_name=f"Cliente {suffix}",
                full_name_normalized=f"cliente {suffix}",
                phone=phone,
                phone_normalized=phone,
                email=f"cliente{suffix}@example.test",
                source=source,
                is_active=True,
                internal_notes="",
            )
            password_hash = make_password(f"legacy-password-{suffix}")
            access = BusinessClientAccess.objects.create(
                business=business,
                business_client=client,
                phone=phone,
                phone_normalized=phone,
                email=f"cliente{suffix}@example.test",
                email_normalized=f"cliente{suffix}@example.test",
                email_verified_at=timezone.now() if verified else None,
                password_hash=password_hash,
                is_active=access_active,
            )
            return client.pk, access.pk, password_hash

        return {
            "legacy_public": create_access(suffix="01", source="other"),
            "verified_public": create_access(suffix="02", source="other", verified=True),
            "professional": create_access(suffix="03", source="professional"),
            "inactive_public": create_access(
                suffix="04",
                source="other",
                access_active=False,
            ),
        }

    def test_only_active_unverified_public_registrations_are_secured(self):
        BusinessClient = self.migrated_apps.get_model("customers", "BusinessClient")
        BusinessClientAccess = self.migrated_apps.get_model(
            "customers",
            "BusinessClientAccess",
        )

        for fixture_name in ("legacy_public", "inactive_public"):
            client_id, access_id, password_hash = self.fixture_ids[fixture_name]
            client = BusinessClient.objects.get(pk=client_id)
            access = BusinessClientAccess.objects.get(pk=access_id)
            self.assertFalse(client.is_active)
            self.assertTrue(access.is_pending_public_registration)
            self.assertNotEqual(access.password_hash, password_hash)
            self.assertFalse(is_password_usable(access.password_hash))

        inactive_access_id = self.fixture_ids["inactive_public"][1]
        self.assertFalse(BusinessClientAccess.objects.get(pk=inactive_access_id).is_active)

        for fixture_name in ("verified_public", "professional"):
            client_id, access_id, password_hash = self.fixture_ids[fixture_name]
            client = BusinessClient.objects.get(pk=client_id)
            access = BusinessClientAccess.objects.get(pk=access_id)
            self.assertTrue(client.is_active)
            self.assertFalse(access.is_pending_public_registration)
            self.assertEqual(access.password_hash, password_hash)

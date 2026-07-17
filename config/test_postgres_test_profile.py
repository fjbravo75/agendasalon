import os

from django.conf import settings
from django.test import SimpleTestCase


class PostgreSQLTestProfileContractTests(SimpleTestCase):
    def test_functional_email_uses_only_the_in_memory_backend(self):
        if os.environ.get("DJANGO_SETTINGS_MODULE") != "config.settings.postgres_test":
            self.skipTest("Contrato exclusivo del perfil PostgreSQL de pruebas.")

        self.assertTrue(settings.AGENDA_TRANSACTIONAL_EMAIL_ENABLED)
        self.assertFalse(settings.AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL)
        self.assertEqual(
            settings.EMAIL_BACKEND,
            "django.core.mail.backends.locmem.EmailBackend",
        )

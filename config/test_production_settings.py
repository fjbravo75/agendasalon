import os
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionEntrypointTests(SimpleTestCase):
    production_variables = (
        "DJANGO_SETTINGS_MODULE",
        "DJANGO_SECRET_KEY",
        "DJANGO_ALLOWED_HOSTS",
        "DJANGO_CSRF_TRUSTED_ORIGINS",
        "DJANGO_DATABASE_URL",
        "AGENDA_PLATFORM_LEGAL_NAME",
        "AGENDA_PLATFORM_TAX_ID",
        "AGENDA_PLATFORM_LEGAL_ADDRESS",
        "AGENDA_PLATFORM_PRIVACY_EMAIL",
        "AGENDA_PLATFORM_WEBSITE",
        "AGENDA_PLATFORM_LEGAL_DEMO",
    )

    def _run_code(self, code, **environment):
        process_environment = os.environ.copy()
        for variable in self.production_variables:
            process_environment.pop(variable, None)
        process_environment.update(environment)
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.fspath(os.path.dirname(os.path.dirname(__file__))),
            env=process_environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def _run_import(self, module, **environment):
        return self._run_code(f"import {module}", **environment)

    def _base_environment(self, **overrides):
        environment = {
            "DJANGO_SECRET_KEY": "test-only-production-secret",
            "DJANGO_ALLOWED_HOSTS": "example.test",
            "DJANGO_DATABASE_URL": (
                "postgresql://agenda:secret@db.example.test:5432/agendasalon?sslmode=require"
            ),
            "AGENDA_PLATFORM_LEGAL_NAME": "AgendaSalon · demostración académica",
            "AGENDA_PLATFORM_PRIVACY_EMAIL": "privacidad@example.test",
            "AGENDA_PLATFORM_WEBSITE": "https://example.test",
            "AGENDA_PLATFORM_LEGAL_DEMO": "1",
        }
        environment.update(overrides)
        return environment

    def test_wsgi_fails_closed_without_production_secrets(self):
        result = self._run_import("config.wsgi")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY is required in production", result.stderr)

    def test_asgi_fails_closed_without_production_secrets(self):
        result = self._run_import("config.asgi")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY is required in production", result.stderr)

    def test_production_requires_a_postgresql_database_url(self):
        result = self._run_import(
            "config.settings.prod",
            DJANGO_SECRET_KEY="test-only-production-secret",
            DJANGO_ALLOWED_HOSTS="example.test",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_DATABASE_URL is required in production", result.stderr)

    def test_academic_demo_keeps_production_security_without_fiscal_identity(self):
        result = self._run_code(
            """
from config.settings import prod
assert prod.DEBUG is False
assert prod.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"
assert prod.SESSION_COOKIE_SECURE is True
assert prod.CSRF_COOKIE_SECURE is True
assert prod.SECURE_SSL_REDIRECT is True
assert prod.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")
assert prod.AGENDA_PLATFORM_LEGAL_DEMO is True
assert prod.AGENDA_PLATFORM_TAX_ID == ""
assert prod.AGENDA_PLATFORM_LEGAL_ADDRESS == ""
""",
            **self._base_environment(),
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_academic_demo_rejects_fiscal_identity_values(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_PLATFORM_TAX_ID="B12345678"),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "AGENDA_PLATFORM_TAX_ID must be empty when AGENDA_PLATFORM_LEGAL_DEMO is enabled",
            result.stderr,
        )

    def test_commercial_mode_still_requires_real_fiscal_data(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_PLATFORM_LEGAL_DEMO="0"),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AGENDA_PLATFORM_TAX_ID is required in production", result.stderr)

    def test_commercial_mode_accepts_complete_legal_identity(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(
                AGENDA_PLATFORM_LEGAL_DEMO="0",
                AGENDA_PLATFORM_LEGAL_NAME="Titular real, S.L.",
                AGENDA_PLATFORM_TAX_ID="B12345678",
                AGENDA_PLATFORM_LEGAL_ADDRESS="Calle Real, 1, Madrid",
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_legal_demo_flag_fails_closed(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_PLATFORM_LEGAL_DEMO="quizas"),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AGENDA_PLATFORM_LEGAL_DEMO must be one of", result.stderr)


class PostgreSQLConfigurationTests(SimpleTestCase):
    def test_database_url_builds_a_postgresql_configuration(self):
        from config.settings.database import postgres_database_config

        config = postgres_database_config(
            "postgresql://agenda%20user:secret%21@db.example.test:5433/agenda%20salon?sslmode=require"
        )

        self.assertEqual(config["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(config["NAME"], "agenda salon")
        self.assertEqual(config["USER"], "agenda user")
        self.assertEqual(config["PASSWORD"], "secret!")
        self.assertEqual(config["HOST"], "db.example.test")
        self.assertEqual(config["PORT"], "5433")
        self.assertEqual(config["OPTIONS"], {"sslmode": "require"})

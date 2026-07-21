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
        "AGENDA_BACKUP_SCHEDULE_CONFIGURED",
        "AGENDA_TRANSACTIONAL_EMAIL_ENABLED",
        "AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED",
        "AGENDA_MANUAL_DEMO_REFRESH_ENABLED",
        "AGENDA_DEMO_SUPERADMIN_PASSWORD",
        "AGENDA_OPERATIONAL_EMAIL_HOURLY_LIMIT",
        "AGENDA_OPERATIONAL_EMAIL_DAILY_LIMIT",
        "AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS",
        "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL",
        "EMAIL_HOST",
        "EMAIL_PORT",
        "EMAIL_HOST_USER",
        "EMAIL_HOST_PASSWORD",
        "DEFAULT_FROM_EMAIL",
        "EMAIL_TIMEOUT",
        "AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS",
        "EMAIL_USE_TLS",
        "EMAIL_USE_SSL",
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
            "AGENDA_BACKUP_SCHEDULE_CONFIGURED": "0",
            "AGENDA_TRANSACTIONAL_EMAIL_ENABLED": "0",
            "AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED": "0",
            "AGENDA_MANUAL_DEMO_REFRESH_ENABLED": "0",
            "AGENDA_DEMO_SUPERADMIN_PASSWORD": "AgendaSalonDemo1",
            "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL": "0",
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
assert prod.AGENDA_BACKUP_SCHEDULE_CONFIGURED is True
assert prod.AGENDA_PLATFORM_TAX_ID == ""
assert prod.AGENDA_PLATFORM_LEGAL_ADDRESS == ""
""",
            **self._base_environment(AGENDA_BACKUP_SCHEDULE_CONFIGURED="1"),
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

    def test_academic_demo_requires_a_superadmin_password(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_DEMO_SUPERADMIN_PASSWORD=""),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "AGENDA_DEMO_SUPERADMIN_PASSWORD is required in production",
            result.stderr,
        )

    def test_academic_demo_rejects_a_short_superadmin_password(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(
                AGENDA_DEMO_SUPERADMIN_PASSWORD="AgendaSalon1"
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must contain at least 16 characters", result.stderr)

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

    def test_invalid_backup_schedule_flag_fails_closed(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_BACKUP_SCHEDULE_CONFIGURED="quizas"),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AGENDA_BACKUP_SCHEDULE_CONFIGURED must be one of", result.stderr)

    def test_transactional_email_requires_smtp_credentials_when_enabled(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_TRANSACTIONAL_EMAIL_ENABLED="1"),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("EMAIL_HOST is required in production", result.stderr)

    def test_transactional_email_accepts_a_complete_smtp_configuration(self):
        result = self._run_code(
            """
from config.settings import prod
assert prod.AGENDA_TRANSACTIONAL_EMAIL_ENABLED is True
assert prod.AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL is False
assert prod.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"
assert prod.EMAIL_USE_TLS is True
assert prod.EMAIL_USE_SSL is False
assert prod.EMAIL_TIMEOUT == 20
assert prod.AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS == 120
""",
            **self._base_environment(
                AGENDA_TRANSACTIONAL_EMAIL_ENABLED="1",
                EMAIL_HOST="smtp.example.test",
                EMAIL_PORT="587",
                EMAIL_HOST_USER="agenda@example.test",
                EMAIL_HOST_PASSWORD="test-only-password",
                DEFAULT_FROM_EMAIL="AgendaSalon <agenda@example.test>",
                EMAIL_USE_TLS="1",
                EMAIL_USE_SSL="0",
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_disabled_transactional_email_uses_a_non_smtp_backend(self):
        result = self._run_code(
            """
from config.settings import prod
assert prod.AGENDA_TRANSACTIONAL_EMAIL_ENABLED is False
assert prod.AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL is False
assert prod.EMAIL_BACKEND == "django.core.mail.backends.dummy.EmailBackend"
""",
            **self._base_environment(),
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_demo_suppression_overrides_a_complete_smtp_configuration(self):
        result = self._run_code(
            """
from config.settings import prod
assert prod.AGENDA_TRANSACTIONAL_EMAIL_ENABLED is True
assert prod.AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL is True
assert prod.EMAIL_BACKEND == "django.core.mail.backends.dummy.EmailBackend"
""",
            **self._base_environment(
                AGENDA_TRANSACTIONAL_EMAIL_ENABLED="1",
                AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL="1",
                EMAIL_HOST="smtp.example.test",
                EMAIL_PORT="587",
                EMAIL_HOST_USER="agenda@example.test",
                EMAIL_HOST_PASSWORD="test-only-password",
                DEFAULT_FROM_EMAIL="AgendaSalon <agenda@example.test>",
                EMAIL_USE_TLS="1",
                EMAIL_USE_SSL="0",
            ),
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_demo_suppression_rejects_an_invalid_flag(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL="quizas"),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL must be one of", result.stderr)

    def test_demo_suppression_is_rejected_outside_academic_demo_mode(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(
                AGENDA_PLATFORM_LEGAL_DEMO="0",
                AGENDA_PLATFORM_TAX_ID="B12345678",
                AGENDA_PLATFORM_LEGAL_ADDRESS="Calle Real, 1, Madrid",
                AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL="1",
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL can only be enabled in academic demo mode",
            result.stderr,
        )

    def test_invalid_manual_refresh_flag_fails_closed(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_MANUAL_DEMO_REFRESH_ENABLED="quizas"),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AGENDA_MANUAL_DEMO_REFRESH_ENABLED must be one of", result.stderr)

    def test_manual_refresh_is_rejected_outside_academic_demo_mode(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(
                AGENDA_PLATFORM_LEGAL_DEMO="0",
                AGENDA_PLATFORM_TAX_ID="B12345678",
                AGENDA_PLATFORM_LEGAL_ADDRESS="Calle Real, 1, Madrid",
                AGENDA_MANUAL_DEMO_REFRESH_ENABLED="1",
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "AGENDA_MANUAL_DEMO_REFRESH_ENABLED can only be enabled in academic demo mode",
            result.stderr,
        )

    def test_demo_refresh_recommended_age_must_be_a_positive_integer(self):
        invalid = self._run_import(
            "config.settings.prod",
            **self._base_environment(
                AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS="no-numero"
            ),
        )
        zero = self._run_import(
            "config.settings.prod",
            **self._base_environment(AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS="0"),
        )

        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("must be integers", invalid.stderr)
        self.assertNotEqual(zero.returncode, 0)
        self.assertIn("must be greater than zero", zero.stderr)

    def test_transactional_email_requires_a_lease_longer_than_its_timeout(self):
        result = self._run_import(
            "config.settings.prod",
            **self._base_environment(
                AGENDA_TRANSACTIONAL_EMAIL_ENABLED="1",
                EMAIL_HOST="smtp.example.test",
                EMAIL_PORT="587",
                EMAIL_HOST_USER="agenda@example.test",
                EMAIL_HOST_PASSWORD="test-only-password",
                DEFAULT_FROM_EMAIL="AgendaSalon <agenda@example.test>",
                EMAIL_TIMEOUT="20",
                AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS="20",
                EMAIL_USE_TLS="1",
                EMAIL_USE_SSL="0",
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "AGENDA_OUTBOUND_EMAIL_LEASE_SECONDS must be greater than EMAIL_TIMEOUT",
            result.stderr,
        )


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

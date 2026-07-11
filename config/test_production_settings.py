import os
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionEntrypointTests(SimpleTestCase):
    def _run_import(self, module, **environment):
        process_environment = os.environ.copy()
        for variable in (
            "DJANGO_SETTINGS_MODULE",
            "DJANGO_SECRET_KEY",
            "DJANGO_ALLOWED_HOSTS",
            "DJANGO_CSRF_TRUSTED_ORIGINS",
            "DJANGO_DATABASE_URL",
        ):
            process_environment.pop(variable, None)
        process_environment.update(environment)
        return subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=os.fspath(os.path.dirname(os.path.dirname(__file__))),
            env=process_environment,
            capture_output=True,
            text=True,
            check=False,
        )

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

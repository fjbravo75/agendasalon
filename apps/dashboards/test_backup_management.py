from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from apps.dashboards.models import BackupExecution
from ops.backup_restore import BackupError


@override_settings(MEDIA_ROOT="media-test")
class BackupManagementCommandTests(TestCase):
    def setUp(self):
        self.environment = patch.dict(
            "os.environ",
            {
                "DJANGO_DATABASE_URL": (
                    "postgresql://agenda:secret@db.example.test:5432/agendasalon?sslmode=require"
                ),
                "AGENDA_BACKUP_HMAC_KEY": "clave-separada-de-prueba",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @patch("apps.dashboards.management.commands.backup_agendasalon.verify_backup")
    @patch("apps.dashboards.management.commands.backup_agendasalon.create_backup")
    def test_records_a_verified_external_backup_without_exposing_paths(self, create, verify):
        with TemporaryDirectory() as temporary:
            backup_dir = Path(temporary) / "backup"
            backup_dir.mkdir()
            for filename in ("database.dump", "media.tar.gz", "manifest.json"):
                (backup_dir / filename).write_bytes(b"verified")
            create.return_value = backup_dir

            call_command(
                "backup_agendasalon",
                backup_root=Path(temporary),
                destination=BackupExecution.Destination.EXTERNAL_ENCRYPTED,
            )

        execution = BackupExecution.objects.get()
        self.assertEqual(execution.status, BackupExecution.Status.SUCCEEDED)
        self.assertEqual(execution.destination, BackupExecution.Destination.EXTERNAL_ENCRYPTED)
        self.assertTrue(execution.integrity_verified)
        self.assertTrue(execution.authenticity_verified)
        self.assertGreater(execution.total_size_bytes, 0)
        self.assertFalse(hasattr(execution, "backup_path"))
        verify.assert_called_once()

    @patch(
        "apps.dashboards.management.commands.backup_agendasalon.create_backup",
        side_effect=BackupError("host=db.example.test password=secret"),
    )
    def test_records_only_a_safe_failure_code(self, create):
        with self.assertRaises(CommandError):
            call_command("backup_agendasalon", backup_root=Path("unused"))

        execution = BackupExecution.objects.get()
        self.assertEqual(execution.status, BackupExecution.Status.FAILED)
        self.assertEqual(execution.failure_code, "backup_validation_failed")
        self.assertNotIn("secret", execution.failure_code)
        self.assertNotIn("db.example.test", execution.failure_code)

    def test_requires_operational_secrets_before_creating_a_registry_row(self):
        with patch.dict("os.environ", {"DJANGO_DATABASE_URL": "", "AGENDA_BACKUP_HMAC_KEY": ""}):
            with self.assertRaises(CommandError):
                call_command("backup_agendasalon", backup_root=Path("unused"))

        self.assertFalse(BackupExecution.objects.exists())

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ops.backup_restore import BackupError, create_backup, restore_backup, verify_backup


DATABASE_URL = "postgresql://agenda:secret@db.example.test:5432/agendasalon?sslmode=require"


class BackupRestoreTests(TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / "imagen.webp").write_bytes(b"media-test")

    def tearDown(self):
        self.temporary_directory.cleanup()

    @patch("ops.backup_restore.subprocess.run")
    def test_backup_contains_database_media_checksums_and_no_password(self, run):
        def create_fake_dump(command, **kwargs):
            dump_argument = next(item for item in command if item.startswith("--file="))
            Path(dump_argument.removeprefix("--file=")).write_bytes(b"postgres-dump")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = create_fake_dump
        backup_dir = create_backup(
            database_url=DATABASE_URL,
            media_root=self.media,
            backup_root=self.root / "backups",
            now=datetime(2026, 7, 11, 8, 0, tzinfo=timezone.utc),
        )

        manifest = verify_backup(backup_dir)
        manifest_text = json.dumps(manifest)
        command = run.call_args.args[0]
        environment = run.call_args.kwargs["env"]
        self.assertNotIn("secret", " ".join(command))
        self.assertNotIn("secret", manifest_text)
        self.assertEqual(environment["PGPASSWORD"], "secret")
        self.assertEqual(environment["PGSSLMODE"], "require")

    @patch("ops.backup_restore.subprocess.run")
    def test_restore_verifies_and_recovers_database_and_media(self, run):
        def create_fake_dump(command, **kwargs):
            dump_arguments = [item for item in command if item.startswith("--file=")]
            if dump_arguments:
                Path(dump_arguments[0].removeprefix("--file=")).write_bytes(b"postgres-dump")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = create_fake_dump
        backup_dir = create_backup(
            database_url=DATABASE_URL,
            media_root=self.media,
            backup_root=self.root / "backups",
        )
        media_target = self.root / "restored-media"

        restore_backup(
            database_url=DATABASE_URL,
            backup_dir=backup_dir,
            media_target=media_target,
            confirm_restore=True,
        )

        self.assertEqual((media_target / "imagen.webp").read_bytes(), b"media-test")
        restore_command = run.call_args.args[0]
        self.assertIn("--clean", restore_command)
        self.assertIn("--exit-on-error", restore_command)

    @patch("ops.backup_restore.subprocess.run")
    def test_restore_refuses_a_corrupt_backup(self, run):
        def create_fake_dump(command, **kwargs):
            dump_argument = next(item for item in command if item.startswith("--file="))
            Path(dump_argument.removeprefix("--file=")).write_bytes(b"postgres-dump")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = create_fake_dump
        backup_dir = create_backup(
            database_url=DATABASE_URL,
            media_root=self.media,
            backup_root=self.root / "backups",
        )
        (backup_dir / "database.dump").write_bytes(b"corrupted")

        with self.assertRaises(BackupError):
            restore_backup(
                database_url=DATABASE_URL,
                backup_dir=backup_dir,
                media_target=self.root / "restored-media",
                confirm_restore=True,
            )

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from ops.backup_restore import (
    BackupError,
    apply_retention_plan,
    build_retention_plan,
    check_backup_freshness,
    create_backup,
    restore_backup,
    verify_backup,
)


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

    @patch("ops.backup_restore.subprocess.run")
    def test_authenticated_backup_rejects_a_replaced_artifact_and_manifest(self, run):
        def create_fake_dump(command, **kwargs):
            dump_argument = next(item for item in command if item.startswith("--file="))
            Path(dump_argument.removeprefix("--file=")).write_bytes(b"postgres-dump")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = create_fake_dump
        backup_dir = create_backup(
            database_url=DATABASE_URL,
            media_root=self.media,
            backup_root=self.root / "backups",
            integrity_key="clave-operativa-separada",
        )
        database_dump = backup_dir / "database.dump"
        database_dump.write_bytes(b"dump-sustituido")
        manifest_path = backup_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["database"]["sha256"] = hashlib.sha256(b"dump-sustituido").hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(BackupError, "autenticidad"):
            verify_backup(
                backup_dir,
                integrity_key="clave-operativa-separada",
                require_authenticity=True,
            )


class BackupRetentionTests(TestCase):
    integrity_key = "clave-retencion-separada"

    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.media = self.root / "media"
        self.media.mkdir()
        (self.media / "imagen.webp").write_bytes(b"media-test")

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _create_backups(self, run, dates):
        def create_fake_dump(command, **kwargs):
            dump_argument = next(item for item in command if item.startswith("--file="))
            Path(dump_argument.removeprefix("--file=")).write_bytes(b"postgres-dump")
            return subprocess.CompletedProcess(command, 0)

        run.side_effect = create_fake_dump
        return [
            create_backup(
                database_url=DATABASE_URL,
                media_root=self.media,
                backup_root=self.root / "backups",
                now=value,
                integrity_key=self.integrity_key,
            )
            for value in dates
        ]

    @patch("ops.backup_restore.subprocess.run")
    def test_retention_keeps_daily_weekly_and_monthly_representatives(self, run):
        paths = self._create_backups(
            run,
            (
                datetime(2026, 7, 14, 10, tzinfo=timezone.utc),
                datetime(2026, 7, 14, 9, tzinfo=timezone.utc),
                datetime(2026, 7, 13, 10, tzinfo=timezone.utc),
                datetime(2026, 7, 7, 10, tzinfo=timezone.utc),
                datetime(2026, 6, 30, 10, tzinfo=timezone.utc),
                datetime(2026, 5, 31, 10, tzinfo=timezone.utc),
            ),
        )

        plan = build_retention_plan(
            backup_root=self.root / "backups",
            integrity_key=self.integrity_key,
            daily=2,
            weekly=2,
            monthly=2,
        )

        self.assertEqual(
            {item.path for item in plan.keep},
            {paths[0], paths[2], paths[3], paths[4]},
        )
        self.assertEqual({item.path for item in plan.remove}, {paths[1], paths[5]})
        self.assertTrue(all(path.exists() for path in paths))

    @patch("ops.backup_restore.subprocess.run")
    def test_apply_retention_deletes_only_verified_candidates(self, run):
        paths = self._create_backups(
            run,
            (
                datetime(2026, 7, 14, 10, tzinfo=timezone.utc),
                datetime(2026, 7, 14, 9, tzinfo=timezone.utc),
            ),
        )
        plan = build_retention_plan(
            backup_root=self.root / "backups",
            integrity_key=self.integrity_key,
            daily=1,
            weekly=1,
            monthly=1,
        )

        apply_retention_plan(
            plan,
            backup_root=self.root / "backups",
            integrity_key=self.integrity_key,
        )

        self.assertTrue(paths[0].exists())
        self.assertFalse(paths[1].exists())

    @patch("ops.backup_restore.subprocess.run")
    def test_invalid_candidate_stops_retention_before_deleting(self, run):
        paths = self._create_backups(
            run,
            (
                datetime(2026, 7, 14, 10, tzinfo=timezone.utc),
                datetime(2026, 7, 14, 9, tzinfo=timezone.utc),
            ),
        )
        (paths[1] / "database.dump").write_bytes(b"corrupto")

        with self.assertRaisesRegex(BackupError, "suma de comprobación"):
            build_retention_plan(
                backup_root=self.root / "backups",
                integrity_key=self.integrity_key,
                daily=1,
                weekly=1,
                monthly=1,
            )

        self.assertTrue(all(path.exists() for path in paths))

    @patch("ops.backup_restore.subprocess.run")
    def test_health_rejects_a_stale_backup(self, run):
        self._create_backups(
            run,
            (datetime(2026, 7, 10, 10, tzinfo=timezone.utc),),
        )

        with self.assertRaisesRegex(BackupError, "antigüedad permitida"):
            check_backup_freshness(
                backup_root=self.root / "backups",
                integrity_key=self.integrity_key,
                max_age_hours=36,
                now=datetime(2026, 7, 14, 10, tzinfo=timezone.utc),
            )

    @patch("ops.backup_restore.subprocess.run")
    def test_health_accepts_a_recent_verified_backup(self, run):
        paths = self._create_backups(
            run,
            (datetime(2026, 7, 14, 9, tzinfo=timezone.utc),),
        )

        latest = check_backup_freshness(
            backup_root=self.root / "backups",
            integrity_key=self.integrity_key,
            max_age_hours=36,
            now=datetime(2026, 7, 14, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(latest.path, paths[0])

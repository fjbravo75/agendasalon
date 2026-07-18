from __future__ import annotations

import io
import json
import shutil
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, transaction
from django.test import SimpleTestCase, TransactionTestCase

from apps.businesses.models import Business
from apps.core.demo_integrity import (
    DemoIntegrityError,
    DemoRefreshGuard,
    QuiescenceMarker,
    acquire_application_table_locks,
    assert_model_reset_contract,
    boe_signature,
    canonicalize_boe_catalog,
    delete_mutable_demo_data,
    demo_semantic_fingerprint,
    expected_database_tables,
    protected_records_signature,
    required_boe_years,
    validate_boe_coverage,
)
from apps.core.demo_refresh_requests import (
    claim_pending_demo_refresh,
    finalize_demo_refresh,
    request_demo_refresh,
)
from apps.core.management.commands.refresh_demo import Command
from apps.core.management.commands.seed_demo import DemoSeeder
from apps.core.models import DemoRefreshReceipt, DemoRefreshRequest
from apps.customers.models import BusinessClientAccess
from apps.dashboards.models import BackupExecution
from apps.holidays.models import HolidaySyncRun, OfficialHoliday
from apps.holidays.services import BOE_NATIONAL_SOURCE_NAME
from apps.legal.models import (
    CustomerPrivacyEvidenceEvent,
    LegalAcceptanceEvent,
    LegalDocument,
)
from apps.notifications.models import InternalNotification


MADRID = ZoneInfo("Europe/Madrid")


def ensure_minimal_refresh_legal_documents():
    """Crea el contrato legal mínimo que ``seed_demo`` exige tras cada flush."""

    required_kinds = (
        LegalDocument.Kind.TERMS,
        LegalDocument.Kind.PLATFORM_PRIVACY,
        LegalDocument.Kind.DATA_PROCESSING,
        LegalDocument.Kind.CUSTOMER_PRIVACY,
    )
    for kind in required_kinds:
        if LegalDocument.objects.filter(kind=kind, is_active=True).exists():
            continue
        LegalDocument.objects.create(
            kind=kind,
            slug=f"refresh-test-{kind}",
            version="refresh-test-v1",
            title=f"Documento de prueba: {kind}",
            lead="Documento legal mínimo para probar la regeneración aislada.",
            sections=[
                {
                    "heading": "Contrato de prueba",
                    "paragraphs": ["Contenido determinista de integración."],
                }
            ],
            published_at=datetime(2026, 1, 1, 0, 0, tzinfo=MADRID),
            is_active=True,
        )


@contextmanager
def writable_test_directory():
    path = Path.cwd() / f".qa-refresh-{uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class DemoRefreshGuardUnitTests(SimpleTestCase):
    def _settings(self, media_root):
        return SimpleNamespace(
            DEBUG=False,
            AGENDA_PLATFORM_LEGAL_DEMO=True,
            AGENDA_BACKUP_SCHEDULE_CONFIGURED=True,
            AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False,
            AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL=True,
            EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend",
            SECURE_SSL_REDIRECT=True,
            SESSION_COOKIE_SECURE=True,
            CSRF_COOKIE_SECURE=True,
            AGENDA_PLATFORM_WEBSITE="https://agenda.example.test",
            ALLOWED_HOSTS=["agenda.example.test"],
            CSRF_TRUSTED_ORIGINS=["https://agenda.example.test"],
            MEDIA_ROOT=media_root,
        )

    def _connection(self):
        return SimpleNamespace(
            vendor="postgresql",
            settings_dict={
                "NAME": "agenda_demo",
                "USER": "agenda_user",
                "HOST": "127.0.0.1",
                "PORT": "5432",
            },
        )

    def _environ(self, media_root):
        return {
            "DJANGO_SETTINGS_MODULE": "config.settings.prod",
            "AGENDA_DEMO_REFRESH_ENABLED": "1",
            "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL": "1",
            "AGENDA_DEMO_EXPECTED_PLATFORM_WEBSITE": "https://agenda.example.test",
            "AGENDA_DEMO_EXPECTED_MEDIA_ROOT": str(media_root),
            "AGENDA_DEMO_EXPECTED_DATABASE_NAME": "agenda_demo",
            "AGENDA_DEMO_EXPECTED_DATABASE_USER": "agenda_user",
            "AGENDA_DEMO_EXPECTED_DATABASE_HOST": "127.0.0.1",
            "AGENDA_DEMO_EXPECTED_DATABASE_PORT": "5432",
        }

    def test_static_preflight_can_be_tested_without_a_production_database(self):
        with writable_test_directory() as media_root:
            guard = DemoRefreshGuard(
                confirm_full_reset=True,
                environ=self._environ(media_root),
                settings_object=self._settings(media_root),
                database_connection=self._connection(),
            )
            guard._validate_static_contract()

    def test_static_preflight_rejects_a_missing_confirmation(self):
        with writable_test_directory() as media_root:
            guard = DemoRefreshGuard(
                confirm_full_reset=False,
                environ=self._environ(media_root),
                settings_object=self._settings(media_root),
                database_connection=self._connection(),
            )
            with self.assertRaisesMessage(DemoIntegrityError, "--confirm-full-reset"):
                guard._validate_static_contract()

    def test_static_preflight_rejects_email_delivery_or_non_production_settings(self):
        with writable_test_directory() as media_root:
            environ = self._environ(media_root)
            environ["AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL"] = "0"
            guard = DemoRefreshGuard(
                confirm_full_reset=True,
                environ=environ,
                settings_object=self._settings(media_root),
                database_connection=self._connection(),
            )
            with self.assertRaisesMessage(
                DemoIntegrityError,
                "AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL",
            ):
                guard._validate_static_contract()

            environ["AGENDA_DEMO_SUPPRESS_OUTBOUND_EMAIL"] = "1"
            environ["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"
            with self.assertRaisesMessage(DemoIntegrityError, "config.settings.prod"):
                guard._validate_static_contract()

    def test_database_identity_binds_name_user_host_and_real_port(self):
        with writable_test_directory() as media_root:
            database_connection = MagicMock()
            database_connection.vendor = "postgresql"
            database_connection.settings_dict = self._connection().settings_dict
            cursor = database_connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = ("agenda_demo", "agenda_user", 5432)
            guard = DemoRefreshGuard(
                confirm_full_reset=True,
                environ=self._environ(media_root),
                settings_object=self._settings(media_root),
                database_connection=database_connection,
            )

            guard._validate_database_identity()

            sql = cursor.execute.call_args.args[0]
            self.assertIn("inet_server_port()", sql)

            cursor.fetchone.return_value = ("agenda_demo", "agenda_user", 6432)
            with self.assertRaisesMessage(DemoIntegrityError, "otro puerto"):
                guard._validate_database_identity()

    def test_database_role_query_accepts_only_primary_writable_unprivileged_role(self):
        with writable_test_directory() as media_root:
            database_connection = MagicMock()
            database_connection.vendor = "postgresql"
            database_connection.settings_dict = self._connection().settings_dict
            cursor = database_connection.cursor.return_value.__enter__.return_value
            cursor.fetchone.side_effect = [
                (
                    "agenda_user",
                    "public",
                    False,
                    "off",
                    False,
                    False,
                    False,
                    False,
                    False,
                ),
                (0,),
            ]
            guard = DemoRefreshGuard(
                confirm_full_reset=True,
                environ=self._environ(media_root),
                settings_object=self._settings(media_root),
                database_connection=database_connection,
            )

            guard._validate_database_role_and_connections()

            role_sql = cursor.execute.call_args_list[0].args[0]
            self.assertIn("pg_is_in_recovery()", role_sql)
            self.assertIn("rolbypassrls", role_sql)

            cursor.reset_mock()
            cursor.fetchone.side_effect = [
                (
                    "agenda_user",
                    "public",
                    False,
                    "off",
                    True,
                    False,
                    False,
                    False,
                    False,
                )
            ]
            with self.assertRaisesMessage(DemoIntegrityError, "privilegios globales"):
                guard._validate_database_role_and_connections()

    def test_application_table_lock_contract_uses_access_exclusive_nowait(self):
        database_connection = MagicMock()
        database_connection.vendor = "postgresql"
        database_connection.in_atomic_block = True
        database_connection.ops.quote_name.side_effect = lambda value: f'"{value}"'
        cursor = database_connection.cursor.return_value.__enter__.return_value

        with patch("apps.core.demo_integrity.connection", database_connection):
            acquire_application_table_locks()

        lock_calls = cursor.execute.call_args_list
        self.assertEqual(len(lock_calls), len(expected_database_tables()) - 1)
        self.assertTrue(
            all(
                " IN ACCESS EXCLUSIVE MODE NOWAIT" in call.args[0]
                for call in lock_calls
            )
        )

    def test_marker_binds_nonce_backup_quarantine_and_empty_media_root(self):
        with writable_test_directory() as root:
            media_root = root / "media"
            backup = root / "backup"
            quarantine = root / "quarantine"
            media_root.mkdir()
            backup.mkdir()
            quarantine.mkdir()
            marker = root / "marker"
            run_id = "refresh-20260717-abcdef0123456789"
            marker.write_text(
                "\n".join(
                    (
                        f"run_id={run_id}",
                        "created_at=999990",
                        f"backup_dir={backup}",
                        f"media_quarantine={quarantine}",
                        f"media_root={media_root}",
                    )
                ),
                encoding="utf-8",
            )
            environ = self._environ(media_root)
            environ.update(
                {
                    "AGENDA_DEMO_REFRESH_RUN_ID": run_id,
                    "AGENDA_DEMO_QUIESCENCE_MARKER": str(marker),
                }
            )
            guard = DemoRefreshGuard(
                confirm_full_reset=True,
                environ=environ,
                settings_object=self._settings(media_root),
                database_connection=self._connection(),
                now_epoch=lambda: 1_000_000,
            )

            resolved_media = guard._validate_media_root()
            real_path_stat = Path.stat

            def stat_with_root_owned_marker(path, *args, **kwargs):
                result = real_path_stat(path, *args, **kwargs)
                if path == marker:
                    return SimpleNamespace(st_mode=result.st_mode, st_uid=0)
                return result

            # El runner de CI usa un usuario sin privilegios. Simulamos solo
            # el propietario del marcador; la guarda de producción continúa
            # exigiendo UID 0 y el resto de rutas conserva su stat real.
            with patch.object(Path, "stat", new=stat_with_root_owned_marker):
                parsed = guard._validate_marker(resolved_media)

            self.assertEqual(parsed.run_id, run_id)
            self.assertEqual(parsed.backup_dir, backup.resolve())
            self.assertEqual(parsed.media_quarantine, quarantine.resolve())

    def test_media_root_with_any_residue_fails_closed(self):
        with writable_test_directory() as media_root:
            (media_root / "residuo.jpg").write_bytes(b"not-an-image")
            guard = DemoRefreshGuard(
                confirm_full_reset=True,
                environ=self._environ(media_root),
                settings_object=self._settings(media_root),
                database_connection=self._connection(),
            )
            with self.assertRaisesMessage(DemoIntegrityError, "debe estar vacío"):
                guard._validate_media_root()

    def test_model_and_table_contract_classifies_every_installed_model(self):
        assert_model_reset_contract()
        tables = expected_database_tables()
        self.assertIn("django_migrations", tables)
        self.assertIn("legal_legaldocument", tables)
        self.assertIn("holidays_officialholiday", tables)

    def test_required_boe_years_cover_the_edges_of_the_seed_window(self):
        self.assertEqual(required_boe_years(date(2026, 7, 17)), (2026,))
        self.assertEqual(required_boe_years(date(2026, 1, 10)), (2025, 2026))
        self.assertEqual(required_boe_years(date(2026, 12, 10)), (2026, 2027))
        self.assertEqual(
            required_boe_years(
                date(2027, 1, 10),
                reference_date=date(2026, 7, 17),
            ),
            (2026, 2027),
        )

    def test_command_fails_before_writing_when_confirmation_is_missing(self):
        with self.assertRaisesMessage(CommandError, "--confirm-full-reset"):
            call_command("refresh_demo", stdout=io.StringIO())

    @patch("apps.core.management.commands.refresh_demo.Command._refresh")
    @patch("apps.core.management.commands.refresh_demo.DemoRefreshGuard.validate")
    def test_command_orchestration_can_be_isolated_from_production(
        self,
        validate_guard,
        refresh,
    ):
        validate_guard.return_value = QuiescenceMarker(
            run_id="refresh-20260717-abcdef0123456789",
            created_at=1.0,
            backup_dir=Path("/backup"),
            media_quarantine=Path("/quarantine"),
            media_root=Path("/media"),
        )
        refresh.return_value = {
            "anchor_date": "2026-07-17",
            "fingerprint": "a" * 64,
        }
        stdout = io.StringIO()

        call_command(
            "refresh_demo",
            confirm_full_reset=True,
            base_date="2026-07-17",
            stdout=stdout,
        )

        refresh.assert_called_once_with(
            anchor_date=date(2026, 7, 17),
            run_id="refresh-20260717-abcdef0123456789",
        )
        self.assertIn("regenerada y verificada", stdout.getvalue())

class DemoRefreshDatabaseTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        ensure_minimal_refresh_legal_documents()

    def _seed(self):
        call_command("seed_demo", base_date="2026-07-17", stdout=io.StringIO())

    def _full_reset(self):
        reference_now = datetime(2026, 7, 17, 4, 5, tzinfo=MADRID)
        with transaction.atomic():
            delete_mutable_demo_data()
            DemoSeeder(
                anchor_date=date(2026, 7, 17),
                reference_now=reference_now,
            ).run()

    def _create_boe_catalog(
        self,
        *,
        year=2026,
        reference="BOE-A-2025-24617",
        finished_at=None,
        created_by=None,
    ):
        source_url = f"https://www.boe.es/diario_boe/txt.php?id={reference}"
        finished_at = finished_at or datetime(2025, 10, 28, 8, 0, tzinfo=MADRID)
        holidays = []
        for month, day in ((1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (12, 25)):
            holidays.append(
                OfficialHoliday.objects.create(
                    date=date(year, month, day),
                    name=f"Festivo {month}-{day}",
                    scope=OfficialHoliday.Scope.NATIONAL,
                    year=year,
                    source_name=BOE_NATIONAL_SOURCE_NAME,
                    source_url=source_url,
                    official_reference=reference,
                )
            )
        run = HolidaySyncRun.objects.create(
            year=year,
            source_name=BOE_NATIONAL_SOURCE_NAME,
            source_url=source_url,
            official_reference=reference,
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=finished_at - timedelta(minutes=1),
            finished_at=finished_at,
            items_loaded=len(holidays),
            items_created=len(holidays),
            created_by=created_by,
        )
        return run, tuple(holidays)

    def test_receipt_query_returns_only_the_committed_operational_result(self):
        run_id = "refresh-20260717-fedcba9876543210"
        fingerprint = demo_semantic_fingerprint()
        DemoRefreshReceipt.objects.create(
            run_id=run_id,
            base_date=date(2026, 7, 17),
            fingerprint=fingerprint,
        )
        stdout = io.StringIO()

        call_command("check_demo_refresh_receipt", run_id=run_id, stdout=stdout)

        payload = json.loads(stdout.getvalue())
        self.assertIs(payload["committed"], True)
        self.assertEqual(payload["run_id"], run_id)
        self.assertEqual(payload["base_date"], "2026-07-17")
        self.assertEqual(payload["fingerprint"], fingerprint)
        self.assertIn("completed_at", payload)

        absent_stdout = io.StringIO()
        call_command(
            "check_demo_refresh_receipt",
            run_id="refresh-20260717-0000000000000000",
            stdout=absent_stdout,
        )
        self.assertEqual(
            json.loads(absent_stdout.getvalue()),
            {
                "committed": False,
                "run_id": "refresh-20260717-0000000000000000",
            },
        )
        with self.assertRaisesMessage(CommandError, "formato seguro"):
            call_command(
                "check_demo_refresh_receipt",
                run_id="../../no-es-un-nonce",
                stdout=io.StringIO(),
            )

        get_user_model().objects.create_user(
            normalized_phone="+34600999998",
            password="Temporal-segura-2026!",
            full_name="Cambio posterior",
        )
        with self.assertRaisesMessage(CommandError, "huella actual no coincide"):
            call_command(
                "check_demo_refresh_receipt",
                run_id=run_id,
                stdout=io.StringIO(),
            )

    def test_receipt_query_reports_database_failure_as_indeterminate(self):
        run_id = "refresh-20260717-5555555555555555"
        DemoRefreshReceipt.objects.create(
            run_id=run_id,
            base_date=date(2026, 7, 17),
            fingerprint=demo_semantic_fingerprint(),
        )
        with patch(
            "apps.core.management.commands.check_demo_refresh_receipt."
            "demo_semantic_fingerprint",
            side_effect=DatabaseError("fallo inyectado"),
        ):
            with self.assertRaisesMessage(CommandError, "Estado indeterminado"):
                call_command(
                    "check_demo_refresh_receipt",
                    run_id=run_id,
                    stdout=io.StringIO(),
                )

    def test_receipt_rejects_a_residual_model_created_after_commit(self):
        run_id = "refresh-20260717-6666666666666666"
        DemoRefreshReceipt.objects.create(
            run_id=run_id,
            base_date=date(2026, 7, 17),
            fingerprint=demo_semantic_fingerprint(),
        )
        Group.objects.create(name="Residuo posterior del evaluador")

        with self.assertRaisesMessage(CommandError, "huella actual no coincide"):
            call_command(
                "check_demo_refresh_receipt",
                run_id=run_id,
                stdout=io.StringIO(),
            )

    def test_allowlist_deletes_append_only_events_and_noncanonical_users(self):
        self._seed()
        receipt = DemoRefreshReceipt.objects.create(
            run_id="refresh-20260717-1111111111111111",
            base_date=date(2026, 7, 17),
            fingerprint="b" * 64,
        )
        User = get_user_model()
        canonical_ids = dict(
            User.objects.values_list("normalized_phone", "pk")
        )
        User.objects.create_user(
            normalized_phone="+34600999999",
            password="Temporal-segura-2026!",
            full_name="Evaluador temporal",
        )
        group = Group.objects.create(name="Permiso temporal de evaluación")
        canonical = User.objects.get(normalized_phone="+34600111001")
        permission = Permission.objects.order_by("pk").first()
        canonical.groups.add(group)
        canonical.user_permissions.add(permission)
        group.permissions.add(permission)
        self.assertTrue(LegalAcceptanceEvent.objects.exists())
        self.assertTrue(CustomerPrivacyEvidenceEvent.objects.exists())

        with transaction.atomic():
            delete_mutable_demo_data()

        self.assertFalse(Business.objects.exists())
        self.assertFalse(LegalAcceptanceEvent._base_manager.exists())
        self.assertFalse(CustomerPrivacyEvidenceEvent._base_manager.exists())
        self.assertEqual(
            dict(User.objects.values_list("normalized_phone", "pk")),
            canonical_ids,
        )
        self.assertFalse(Group.objects.exists())
        self.assertFalse(User.groups.through.objects.exists())
        self.assertFalse(User.user_permissions.through.objects.exists())
        self.assertFalse(Group.permissions.through.objects.exists())
        self.assertTrue(DemoRefreshReceipt.objects.filter(pk=receipt.pk).exists())

    def test_boe_coverage_requires_a_complete_traced_catalogue(self):
        year = 2026
        reference = "BOE-A-2025-24617"
        source_url = f"https://www.boe.es/diario_boe/txt.php?id={reference}"
        now = datetime(2025, 10, 28, 8, 0, tzinfo=MADRID)
        for month, day in ((1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (12, 25)):
            OfficialHoliday.objects.create(
                date=date(year, month, day),
                name=f"Festivo {month}-{day}",
                scope=OfficialHoliday.Scope.NATIONAL,
                year=year,
                source_name=BOE_NATIONAL_SOURCE_NAME,
                source_url=source_url,
                official_reference=reference,
            )
        HolidaySyncRun.objects.create(
            year=year,
            source_name=BOE_NATIONAL_SOURCE_NAME,
            source_url=source_url,
            official_reference=reference,
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=now,
            finished_at=now,
            items_loaded=6,
            items_created=6,
        )

        self.assertEqual(validate_boe_coverage(date(2026, 7, 17)), (2026,))
        OfficialHoliday.objects.filter(year=year).first().delete()
        with self.assertRaisesMessage(DemoIntegrityError, "incompleta"):
            validate_boe_coverage(date(2026, 7, 17))

    def test_boe_canonicalization_keeps_last_real_valid_catalogue_and_removes_residue(self):
        self._seed()
        author = get_user_model().objects.get(normalized_phone="+34600111001")
        kept_run, kept_holidays = self._create_boe_catalog(created_by=author)
        adjacent_run, adjacent_holidays = self._create_boe_catalog(
            year=2027,
            reference="BOE-A-2026-25000",
            finished_at=datetime(2026, 10, 28, 8, 0, tzinfo=MADRID),
            created_by=author,
        )
        later = datetime(2025, 10, 29, 8, 0, tzinfo=MADRID)
        invalid_reference = "BOE-A-2025-99999"
        HolidaySyncRun.objects.create(
            year=2026,
            source_name=BOE_NATIONAL_SOURCE_NAME,
            source_url=(
                "https://www.boe.es/diario_boe/txt.php?id=" f"{invalid_reference}"
            ),
            official_reference=invalid_reference,
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=later - timedelta(minutes=1),
            finished_at=later,
            items_loaded=6,
            items_created=6,
            created_by=author,
        )
        HolidaySyncRun.objects.create(
            year=2026,
            source_name=BOE_NATIONAL_SOURCE_NAME,
            status=HolidaySyncRun.Status.FAILED,
            started_at=later,
            finished_at=later,
            error_detail="fallo de evaluación",
            created_by=author,
        )
        HolidaySyncRun.objects.create(
            year=2030,
            source_name="Fuente temporal",
            status=HolidaySyncRun.Status.PARTIAL,
            started_at=later,
            finished_at=later,
            created_by=author,
        )
        OfficialHoliday.objects.create(
            date=date(2030, 2, 3),
            name="Festivo temporal",
            scope=OfficialHoliday.Scope.LOCAL,
            year=2030,
            source_name="Fuente temporal",
            source_url="https://example.test/fuente-temporal",
            official_reference="TEMPORAL",
        )

        with transaction.atomic():
            self.assertEqual(
                canonicalize_boe_catalog(date(2026, 7, 17)),
                (2026, 2027),
            )

        kept_run.refresh_from_db()
        adjacent_run.refresh_from_db()
        self.assertIsNone(kept_run.created_by_id)
        self.assertIsNone(adjacent_run.created_by_id)
        self.assertEqual(
            set(HolidaySyncRun.objects.values_list("pk", flat=True)),
            {kept_run.pk, adjacent_run.pk},
        )
        self.assertEqual(
            set(OfficialHoliday.objects.values_list("pk", flat=True)),
            {
                holiday.pk
                for holiday in (*kept_holidays, *adjacent_holidays)
            },
        )
        self.assertEqual(validate_boe_coverage(date(2026, 7, 17)), (2026,))
        self.assertEqual(validate_boe_coverage(date(2026, 12, 10)), (2026, 2027))
        first_signature = boe_signature()
        with transaction.atomic():
            self.assertEqual(
                canonicalize_boe_catalog(date(2026, 7, 17)),
                (2026, 2027),
            )
        self.assertEqual(boe_signature(), first_signature)

    def test_protected_global_records_keep_their_exact_signature(self):
        self._seed()
        now = datetime(2026, 7, 16, 8, 0, tzinfo=MADRID)
        previous_receipt = DemoRefreshReceipt.objects.create(
            run_id="refresh-20260716-4444444444444444",
            base_date=date(2026, 7, 16),
            fingerprint="d" * 64,
            completed_at=now,
        )
        active_request = DemoRefreshRequest.objects.create(
            requested_by=get_user_model().objects.get(is_superuser=True),
            base_date=date(2026, 7, 16),
            status=DemoRefreshRequest.Status.PROCESSING,
            requested_at=now,
            started_at=now,
            origin_digest="e" * 64,
        )
        OfficialHoliday.objects.create(
            date=date(2030, 1, 1),
            name="Año Nuevo",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2030,
            source_name="BOE",
            source_url="https://www.boe.es/",
            official_reference="BOE-2030",
        )
        HolidaySyncRun.objects.create(
            year=2030,
            source_name="BOE",
            source_url="https://www.boe.es/",
            official_reference="BOE-2030",
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=now,
            finished_at=now,
            items_loaded=1,
        )
        BackupExecution.objects.create(
            status=BackupExecution.Status.SUCCEEDED,
            started_at=now,
            finished_at=now,
            database_included=True,
            media_included=True,
            integrity_verified=True,
            authenticity_verified=True,
        )
        legal_document_ids = tuple(LegalDocument.objects.values_list("pk", flat=True))
        before = protected_records_signature()

        self._full_reset()

        self.assertEqual(protected_records_signature(), before)
        self.assertEqual(
            DemoRefreshReceipt.objects.get(pk=previous_receipt.pk).fingerprint,
            "d" * 64,
        )
        active_request.refresh_from_db()
        self.assertEqual(active_request.status, DemoRefreshRequest.Status.PROCESSING)
        self.assertIsNone(active_request.receipt)
        self.assertEqual(
            tuple(LegalDocument.objects.values_list("pk", flat=True)),
            legal_document_ids,
        )

    def test_two_full_resets_have_the_same_semantic_fingerprint(self):
        self._seed()
        self._full_reset()
        first = demo_semantic_fingerprint()

        self._full_reset()
        second = demo_semantic_fingerprint()

        self.assertEqual(first, second)
        self.assertEqual(Business.objects.count(), 2)
        self.assertEqual(get_user_model().objects.count(), 3)

    def test_fingerprint_checks_client_passwords_without_rehash_and_notification_times(self):
        self._seed()
        canonical_fingerprint = demo_semantic_fingerprint()
        access = BusinessClientAccess.objects.order_by("email_normalized").first()
        original_hash = access.password_hash

        self.assertEqual(demo_semantic_fingerprint(), canonical_fingerprint)
        access.refresh_from_db()
        self.assertEqual(access.password_hash, original_hash)

        access.set_password("Cambio-temporal-evaluador-2026!")
        access.save(update_fields=["password_hash", "updated_at"])
        changed_hash = access.password_hash
        self.assertNotEqual(demo_semantic_fingerprint(), canonical_fingerprint)
        access.refresh_from_db()
        self.assertEqual(access.password_hash, changed_hash)

        self._full_reset()
        restored_fingerprint = demo_semantic_fingerprint()
        notification = InternalNotification.objects.order_by("created_at").first()
        InternalNotification.objects.filter(pk=notification.pk).update(
            created_at=notification.created_at + timedelta(seconds=1),
            read_at=notification.created_at,
        )
        self.assertNotEqual(demo_semantic_fingerprint(), restored_fingerprint)

    @patch("apps.core.management.commands.refresh_demo.validate_no_other_client_connections")
    @patch("apps.core.management.commands.refresh_demo.canonicalize_boe_catalog")
    @patch("apps.core.management.commands.refresh_demo.validate_boe_coverage")
    @patch("apps.core.management.commands.refresh_demo.acquire_refresh_locks")
    def test_successful_refresh_persists_its_receipt_in_the_same_result(
        self,
        acquire_locks,
        validate_coverage,
        canonicalize_boe,
        validate_connections,
    ):
        self._seed()
        run_id = "refresh-20260717-2222222222222222"

        result = Command()._refresh(
            anchor_date=date(2026, 7, 17),
            run_id=run_id,
        )

        receipt = DemoRefreshReceipt.objects.get(run_id=run_id)
        self.assertEqual(receipt.base_date, date(2026, 7, 17))
        self.assertEqual(receipt.fingerprint, result["fingerprint"])
        self.assertEqual(result["run_id"], run_id)
        acquire_locks.assert_called_once_with(boe_years=(2026,))
        canonicalize_boe.assert_called_once_with(
            date(2026, 7, 17),
            reference_date=date(2026, 7, 17),
        )
        validate_coverage.assert_called_once_with(
            date(2026, 7, 17),
            reference_date=date(2026, 7, 17),
        )
        validate_connections.assert_called_once_with()

    @patch("apps.core.management.commands.refresh_demo.validate_no_other_client_connections")
    @patch("apps.core.management.commands.refresh_demo.canonicalize_boe_catalog")
    @patch("apps.core.management.commands.refresh_demo.validate_boe_coverage")
    @patch("apps.core.management.commands.refresh_demo.acquire_refresh_locks")
    def test_manual_request_refresh_and_finalization_complete_one_real_cycle(
        self,
        acquire_locks,
        validate_coverage,
        canonicalize_boe,
        validate_connections,
    ):
        self._seed()
        actor = get_user_model().objects.get(normalized_phone="+34910000001")
        with self.settings(
            AGENDA_PLATFORM_LEGAL_DEMO=True,
            AGENDA_MANUAL_DEMO_REFRESH_ENABLED=True,
            AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED=False,
        ):
            requested = request_demo_refresh(actor=actor, origin_digest="f" * 64)
            claimed = claim_pending_demo_refresh().refresh_request
            result = Command()._refresh(
                anchor_date=claimed.base_date,
                run_id=str(claimed.public_id),
            )
            completed = finalize_demo_refresh(
                public_id=claimed.public_id,
                succeeded=True,
            )

        self.assertEqual(requested.pk, completed.pk)
        self.assertEqual(completed.status, DemoRefreshRequest.Status.COMPLETED)
        self.assertEqual(completed.receipt.run_id, str(completed.public_id))
        self.assertEqual(completed.receipt.fingerprint, result["fingerprint"])
        self.assertEqual(Business.objects.count(), 2)
        self.assertEqual(get_user_model().objects.count(), 3)
        acquire_locks.assert_called_once_with(boe_years=(2026,))
        canonicalize_boe.assert_called_once()
        validate_coverage.assert_called_once()
        validate_connections.assert_called_once_with()

    @patch("apps.core.management.commands.refresh_demo.validate_no_other_client_connections")
    @patch("apps.core.management.commands.refresh_demo.canonicalize_boe_catalog")
    @patch("apps.core.management.commands.refresh_demo.validate_boe_coverage")
    @patch("apps.core.management.commands.refresh_demo.acquire_refresh_locks")
    def test_database_changes_roll_back_if_the_seeder_fails(
        self,
        acquire_locks,
        validate_coverage,
        canonicalize_boe,
        validate_connections,
    ):
        self._seed()
        original_slugs = set(Business.objects.values_list("slug", flat=True))
        command = Command()

        with patch.object(
            DemoSeeder,
            "run",
            side_effect=DemoIntegrityError("fallo inyectado tras el borrado"),
        ):
            with self.assertRaisesMessage(DemoIntegrityError, "fallo inyectado"):
                command._refresh(
                    anchor_date=date(2026, 7, 17),
                    run_id="refresh-20260717-abcdef0123456789",
                )

        acquire_locks.assert_called_once_with(boe_years=(2026,))
        canonicalize_boe.assert_called_once_with(
            date(2026, 7, 17),
            reference_date=date(2026, 7, 17),
        )
        validate_coverage.assert_called_once_with(
            date(2026, 7, 17),
            reference_date=date(2026, 7, 17),
        )
        validate_connections.assert_not_called()
        self.assertFalse(
            DemoRefreshReceipt.objects.filter(
                run_id="refresh-20260717-abcdef0123456789"
            ).exists()
        )
        self.assertEqual(
            set(Business.objects.values_list("slug", flat=True)),
            original_slugs,
        )

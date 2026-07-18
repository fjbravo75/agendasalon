from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Barrier
from unittest import skipUnless
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.test import TransactionTestCase

from apps.businesses.models import Business
from apps.core.demo_integrity import (
    BOE_ADVISORY_LOCK_NAMESPACE,
    BOE_TRANSACTION_ADVISORY_LOCK_ID,
    DEMO_SEED_LOCK_ID,
    DemoIntegrityError,
    acquire_refresh_locks,
    application_database_tables,
    delete_mutable_demo_data,
    demo_semantic_fingerprint,
    expected_database_tables,
    protected_records_signature,
    validate_no_other_client_connections,
)
from apps.core.demo_refresh_requests import (
    ActiveDemoRefreshRequestExists,
    claim_pending_demo_refresh,
    request_demo_refresh,
)
from apps.core.management.commands.seed_demo import DemoSeeder
from apps.core.models import DemoRefreshReceipt, DemoRefreshRequest
from apps.core.test_refresh_demo import ensure_minimal_refresh_legal_documents


MADRID = ZoneInfo("Europe/Madrid")


@skipUnless(connection.vendor == "postgresql", "Requiere PostgreSQL real.")
class DemoRefreshPostgresTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        super().setUp()
        ensure_minimal_refresh_legal_documents()

    def _seed(self):
        call_command("seed_demo", base_date="2026-07-17", stdout=io.StringIO())

    def _full_reset(self):
        with transaction.atomic():
            acquire_refresh_locks()
            delete_mutable_demo_data()
            DemoSeeder(
                anchor_date=date(2026, 7, 17),
                reference_now=datetime(2026, 7, 17, 4, 5, tzinfo=MADRID),
            ).run()

    def test_seed_and_boe_transaction_locks_exclude_another_connection(self):
        contender = connection.copy(alias="demo_refresh_contender")
        try:
            with transaction.atomic():
                acquire_refresh_locks(boe_years=(2026,))
                contender.ensure_connection()
                with contender.cursor() as cursor:
                    for lock_id in (DEMO_SEED_LOCK_ID, BOE_TRANSACTION_ADVISORY_LOCK_ID):
                        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
                        self.assertFalse(cursor.fetchone()[0])
                    cursor.execute(
                        "SELECT pg_try_advisory_lock(%s, %s)",
                        [BOE_ADVISORY_LOCK_NAMESPACE, 2026],
                    )
                    self.assertFalse(cursor.fetchone()[0])
                contender.close()

            contender.ensure_connection()
            with contender.cursor() as cursor:
                for lock_id in (DEMO_SEED_LOCK_ID, BOE_TRANSACTION_ADVISORY_LOCK_ID):
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
                    self.assertTrue(cursor.fetchone()[0])
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
                    self.assertTrue(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)",
                    [BOE_ADVISORY_LOCK_NAMESPACE, 2026],
                )
                self.assertTrue(cursor.fetchone()[0])
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    [BOE_ADVISORY_LOCK_NAMESPACE, 2026],
                )
                self.assertTrue(cursor.fetchone()[0])
        finally:
            contender.close()

    def test_access_exclusive_locks_block_a_late_reader_and_connection_is_detected(self):
        contender = connection.copy(alias="demo_refresh_table_contender")
        observer = None
        try:
            with transaction.atomic():
                acquire_refresh_locks()
                contender.ensure_connection()
                with contender.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '100ms'")
                    table_name = contender.ops.quote_name(application_database_tables()[0])
                    with self.assertRaises(DatabaseError):
                        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                contender.close()

                observer = connection.Database.connect(**connection.get_connection_params())
                with observer.cursor() as cursor:
                    cursor.execute("SELECT current_database(), pg_backend_pid()")
                    observer_database, observer_pid = cursor.fetchone()
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_database(), pg_backend_pid()")
                    current_database, current_pid = cursor.fetchone()
                self.assertEqual(observer_database, current_database)
                self.assertNotEqual(observer_pid, current_pid)
                with self.assertRaisesMessage(DemoIntegrityError, "otras conexiones"):
                    validate_no_other_client_connections()
                observer.close()
                validate_no_other_client_connections()
        finally:
            contender.close()
            if observer is not None:
                observer.close()

    def test_table_lock_conflict_fails_closed_without_waiting(self):
        contender = connection.copy(alias="demo_refresh_table_blocker")
        try:
            contender.ensure_connection()
            blocked_table = contender.ops.quote_name(application_database_tables()[0])
            with contender.cursor() as cursor:
                cursor.execute("BEGIN")
                cursor.execute(f"LOCK TABLE {blocked_table} IN ACCESS SHARE MODE")

            with self.assertRaisesMessage(DemoIntegrityError, "Otra operación"):
                with transaction.atomic():
                    acquire_refresh_locks()
        finally:
            try:
                with contender.cursor() as cursor:
                    cursor.execute("ROLLBACK")
            finally:
                contender.close()

    def test_real_postgres_full_reset_is_idempotent_and_preserves_global_records(self):
        self._seed()
        before_protected = protected_records_signature()

        self._full_reset()
        first = demo_semantic_fingerprint()
        self._full_reset()
        second = demo_semantic_fingerprint()

        self.assertEqual(first, second)
        self.assertEqual(protected_records_signature(), before_protected)
        self.assertEqual(Business.objects.count(), 2)

    def test_postgres_schema_has_only_the_known_tables(self):
        self.assertEqual(
            set(connection.introspection.table_names()),
            expected_database_tables(),
        )

    def test_receipt_is_visible_to_another_connection_only_after_commit(self):
        run_id = "refresh-20260717-3333333333333333"
        contender = connection.copy(alias="demo_refresh_receipt_contender")
        try:
            contender.ensure_connection()
            with transaction.atomic():
                DemoRefreshReceipt.objects.create(
                    run_id=run_id,
                    base_date=date(2026, 7, 17),
                    fingerprint="c" * 64,
                )
                with contender.cursor() as cursor:
                    cursor.execute(
                        "SELECT COUNT(*) FROM core_demorefreshreceipt WHERE run_id = %s",
                        [run_id],
                    )
                    self.assertEqual(cursor.fetchone()[0], 0)

            with contender.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM core_demorefreshreceipt WHERE run_id = %s",
                    [run_id],
                )
                self.assertEqual(cursor.fetchone()[0], 1)
        finally:
            contender.close()

    def test_two_concurrent_requests_create_exactly_one_active_row(self):
        superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000991",
            password="Concurrency-test-only-2026!",
            full_name="Admin concurrencia",
        )
        barrier = Barrier(2)

        def create_request(suffix):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=superadmin.pk)
                barrier.wait(timeout=10)
                try:
                    request_demo_refresh(
                        actor=actor,
                        origin_digest=suffix * 64,
                    )
                    return "created"
                except ActiveDemoRefreshRequestExists:
                    return "active"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create_request, ("a", "b")))

        self.assertEqual(sorted(results), ["active", "created"])
        self.assertEqual(
            DemoRefreshRequest.objects.filter(
                status__in=(
                    DemoRefreshRequest.Status.PENDING,
                    DemoRefreshRequest.Status.PROCESSING,
                )
            ).count(),
            1,
        )

    def test_two_concurrent_dispatchers_claim_exactly_once(self):
        superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000992",
            password="Concurrency-test-only-2026!",
            full_name="Admin despachador",
        )
        refresh_request = request_demo_refresh(
            actor=superadmin,
            origin_digest="c" * 64,
        )
        barrier = Barrier(2)

        def claim_request(_index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                claim = claim_pending_demo_refresh()
                return str(claim.refresh_request.public_id) if claim else "idle"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim_request, range(2)))

        self.assertEqual(results.count(str(refresh_request.public_id)), 1)
        self.assertEqual(results.count("idle"), 1)
        refresh_request.refresh_from_db()
        self.assertEqual(refresh_request.status, DemoRefreshRequest.Status.PROCESSING)

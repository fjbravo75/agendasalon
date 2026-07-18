from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.demo_refresh_requests import (
    ActiveDemoRefreshRequestExists,
    DemoRefreshFinalizationError,
    claim_pending_demo_refresh,
    finalize_demo_refresh,
    request_demo_refresh,
)
from apps.core.models import DemoRefreshReceipt, DemoRefreshRequest


@override_settings(
    AGENDA_PLATFORM_LEGAL_DEMO=True,
    AGENDA_MANUAL_DEMO_REFRESH_ENABLED=True,
    AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED=False,
)
class DemoRefreshRequestServiceTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000999",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )
        self.origin_digest = "a" * 64

    def _request(self):
        return request_demo_refresh(
            actor=self.superadmin,
            origin_digest=self.origin_digest,
        )

    def test_request_and_claim_use_one_active_row(self):
        refresh_request = self._request()
        with self.assertRaises(ActiveDemoRefreshRequestExists):
            self._request()

        claim = claim_pending_demo_refresh()
        claimed = claim.refresh_request

        self.assertEqual(claimed.pk, refresh_request.pk)
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, DemoRefreshRequest.Status.PROCESSING)
        self.assertIsNotNone(claimed.started_at)
        self.assertIsNone(claim_pending_demo_refresh())

    def test_partial_unique_constraint_rejects_pending_and_processing_together(self):
        self._request()
        with self.assertRaises(IntegrityError), transaction.atomic():
            DemoRefreshRequest.objects.create(
                requested_by=self.superadmin,
                base_date=date(2026, 7, 18),
                status=DemoRefreshRequest.Status.PROCESSING,
                started_at=timezone.now(),
                origin_digest="b" * 64,
            )

    def test_success_requires_and_links_the_exact_receipt(self):
        self._request()
        claimed = claim_pending_demo_refresh().refresh_request
        with self.assertRaises(DemoRefreshFinalizationError):
            finalize_demo_refresh(public_id=claimed.public_id, succeeded=True)

        receipt = DemoRefreshReceipt.objects.create(
            run_id=str(claimed.public_id),
            base_date=claimed.base_date,
            fingerprint="c" * 64,
        )
        completed = finalize_demo_refresh(public_id=claimed.public_id, succeeded=True)

        self.assertEqual(completed.status, DemoRefreshRequest.Status.COMPLETED)
        self.assertEqual(completed.receipt, receipt)
        self.assertIsNotNone(completed.finished_at)

    def test_failure_is_bounded_and_can_preserve_a_committed_receipt(self):
        self._request()
        claimed = claim_pending_demo_refresh().refresh_request
        receipt = DemoRefreshReceipt.objects.create(
            run_id=str(claimed.public_id),
            base_date=claimed.base_date,
            fingerprint="d" * 64,
        )

        failed = finalize_demo_refresh(
            public_id=claimed.public_id,
            succeeded=False,
            failure_code="runtime_rearm_failed",
        )

        self.assertEqual(failed.status, DemoRefreshRequest.Status.FAILED)
        self.assertEqual(failed.receipt, receipt)
        self.assertEqual(failed.failure_code, "runtime_rearm_failed")

    def test_stale_processing_request_fails_closed_without_retry(self):
        self._request()
        claimed = claim_pending_demo_refresh().refresh_request
        stale_started = timezone.now() - timedelta(minutes=61)
        DemoRefreshRequest.objects.filter(pk=claimed.pk).update(started_at=stale_started)

        self.assertIsNone(claim_pending_demo_refresh())

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, DemoRefreshRequest.Status.FAILED)
        self.assertEqual(claimed.failure_code, "dispatcher_interrupted")

    def test_processing_request_with_exact_receipt_is_offered_for_recovery(self):
        self._request()
        claimed = claim_pending_demo_refresh().refresh_request
        receipt = DemoRefreshReceipt.objects.create(
            run_id=str(claimed.public_id),
            base_date=claimed.base_date,
            fingerprint="e" * 64,
        )

        recovery_claim = claim_pending_demo_refresh()
        recoverable = recovery_claim.refresh_request

        self.assertEqual(recoverable.pk, claimed.pk)
        self.assertTrue(recovery_claim.recovery_required)
        self.assertEqual(recoverable.status, DemoRefreshRequest.Status.PROCESSING)
        completed = finalize_demo_refresh(
            public_id=recoverable.public_id,
            succeeded=True,
        )
        self.assertEqual(completed.status, DemoRefreshRequest.Status.COMPLETED)
        self.assertEqual(completed.receipt, receipt)

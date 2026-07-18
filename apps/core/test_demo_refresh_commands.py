from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.core.models import DemoRefreshReceipt, DemoRefreshRequest


@override_settings(
    AGENDA_PLATFORM_LEGAL_DEMO=True,
    AGENDA_MANUAL_DEMO_REFRESH_ENABLED=True,
    AGENDA_OPERATIONAL_NOTIFICATIONS_ENABLED=False,
)
class DemoRefreshDispatcherCommandTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000666",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )

    def test_claim_outputs_only_the_bounded_dispatch_contract(self):
        refresh_request = DemoRefreshRequest.objects.create(
            requested_by=self.superadmin,
            base_date="2026-07-18",
            origin_digest="a" * 64,
        )
        stdout = StringIO()

        call_command("claim_demo_refresh_request", stdout=stdout)

        self.assertEqual(
            stdout.getvalue().strip(),
            f"CLAIMED|{refresh_request.public_id}|2026-07-18",
        )
        refresh_request.refresh_from_db()
        self.assertEqual(refresh_request.status, DemoRefreshRequest.Status.PROCESSING)

    def test_idle_claim_and_completed_finalization_are_explicit(self):
        idle_stdout = StringIO()
        call_command("claim_demo_refresh_request", stdout=idle_stdout)
        self.assertEqual(idle_stdout.getvalue().strip(), "IDLE")

        refresh_request = DemoRefreshRequest.objects.create(
            requested_by=self.superadmin,
            base_date="2026-07-18",
            origin_digest="b" * 64,
        )
        call_command("claim_demo_refresh_request", stdout=StringIO())
        DemoRefreshReceipt.objects.create(
            run_id=str(refresh_request.public_id),
            base_date=refresh_request.base_date,
            fingerprint="c" * 64,
        )
        stdout = StringIO()

        call_command(
            "finalize_demo_refresh_request",
            request_id=str(refresh_request.public_id),
            result="completed",
            stdout=stdout,
        )

        self.assertEqual(
            stdout.getvalue().strip(),
            f"FINALIZED|completed|{refresh_request.public_id}",
        )

    def test_processing_with_receipt_outputs_recovery_contract(self):
        refresh_request = DemoRefreshRequest.objects.create(
            requested_by=self.superadmin,
            base_date="2026-07-18",
            origin_digest="d" * 64,
        )
        call_command("claim_demo_refresh_request", stdout=StringIO())
        DemoRefreshReceipt.objects.create(
            run_id=str(refresh_request.public_id),
            base_date=refresh_request.base_date,
            fingerprint="e" * 64,
        )
        stdout = StringIO()

        call_command("claim_demo_refresh_request", stdout=stdout)

        self.assertEqual(
            stdout.getvalue().strip(),
            f"RECOVER|{refresh_request.public_id}|2026-07-18",
        )

    def test_commands_refuse_to_run_when_the_feature_is_disabled(self):
        with override_settings(AGENDA_MANUAL_DEMO_REFRESH_ENABLED=False):
            with self.assertRaisesMessage(CommandError, "no está habilitada"):
                call_command("claim_demo_refresh_request", stdout=StringIO())

    def test_finalize_rejects_an_invalid_uuid_with_a_bounded_command_error(self):
        with self.assertRaisesMessage(CommandError, "request_missing"):
            call_command(
                "finalize_demo_refresh_request",
                request_id="not-a-uuid",
                result="failed",
                failure_code="orchestrator_failed",
                stdout=StringIO(),
            )

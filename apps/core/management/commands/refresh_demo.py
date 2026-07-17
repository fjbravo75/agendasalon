from __future__ import annotations

import json
from datetime import date, datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.test.utils import override_settings
from django.utils import timezone

from apps.core.demo_integrity import (
    CANONICAL_USER_PHONES,
    DemoIntegrityError,
    DemoRefreshGuard,
    acquire_refresh_locks,
    assert_no_evaluator_residue,
    boe_signature,
    canonicalize_boe_catalog,
    delete_mutable_demo_data,
    demo_semantic_fingerprint,
    protected_records_signature,
    required_boe_years,
    validate_no_other_client_connections,
    validate_boe_coverage,
)
from apps.core.management.commands.seed_demo import DemoSeeder, MADRID


class Command(BaseCommand):
    help = (
        "Regenera por completo la demo académica tras un preflight de producción "
        "y una cuarentena de medios realizada por el orquestador root."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm-full-reset",
            action="store_true",
            help="Confirma de forma explícita el borrado integral de datos mutables.",
        )
        parser.add_argument(
            "--base-date",
            default="",
            help=(
                "Fecha de referencia en formato YYYY-MM-DD. "
                "Si se omite, usa la fecha actual de Madrid."
            ),
        )

    def handle(self, *args, **options):
        anchor_date = self._parse_anchor_date(options["base_date"])
        try:
            marker = DemoRefreshGuard(
                confirm_full_reset=options["confirm_full_reset"]
            ).validate()
            result = self._refresh(anchor_date=anchor_date, run_id=marker.run_id)
        except DemoIntegrityError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS("Demo académica regenerada y verificada sin residuos.")
        )
        self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _parse_anchor_date(raw_value: str) -> date:
        if not raw_value:
            return timezone.localdate()
        try:
            return date.fromisoformat(raw_value)
        except ValueError as exc:
            raise CommandError("--base-date debe usar formato YYYY-MM-DD.") from exc

    @transaction.atomic
    def _refresh(self, *, anchor_date: date, run_id: str) -> dict:
        from django.contrib.auth import get_user_model

        from apps.businesses.models import BusinessSignupRequest
        from apps.core.models import DemoRefreshReceipt
        from apps.notifications.models import OutboundEmail

        current_now = timezone.now().astimezone(MADRID)
        reference_now = min(
            current_now,
            datetime.combine(anchor_date, time(4, 5), tzinfo=MADRID),
        )
        reference_date = reference_now.date()
        boe_years = required_boe_years(
            anchor_date,
            reference_date=reference_date,
        )
        acquire_refresh_locks(boe_years=boe_years)
        if DemoRefreshReceipt._base_manager.filter(run_id=run_id).exists():
            raise DemoIntegrityError("Este nonce ya tiene un refresco confirmado.")
        canonicalize_boe_catalog(anchor_date, reference_date=reference_date)
        validate_boe_coverage(anchor_date, reference_date=reference_date)
        before_protected = protected_records_signature()
        before_boe = boe_signature()
        User = get_user_model()
        preserved_user_ids = dict(
            User._base_manager.filter(
                normalized_phone__in=CANONICAL_USER_PHONES
            ).values_list("normalized_phone", "pk")
        )

        deleted = delete_mutable_demo_data()
        with override_settings(
            AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False,
            EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend",
        ):
            summary = DemoSeeder(
                anchor_date=anchor_date,
                reference_now=reference_now,
            ).run()

        after_boe = boe_signature()
        after_protected = protected_records_signature()
        if after_boe != before_boe:
            raise DemoIntegrityError("La firma BOE cambió durante la regeneración.")
        if after_protected != before_protected:
            raise DemoIntegrityError(
                "Cambió un registro global protegido durante la regeneración."
            )

        current_user_ids = dict(
            User._base_manager.filter(
                normalized_phone__in=CANONICAL_USER_PHONES
            ).values_list("normalized_phone", "pk")
        )
        for phone, original_pk in preserved_user_ids.items():
            if current_user_ids.get(phone) != original_pk:
                raise DemoIntegrityError(
                    f"La identidad canónica {phone} no conservó su fila."
                )
        if set(current_user_ids) != set(CANONICAL_USER_PHONES):
            raise DemoIntegrityError("No quedaron exactamente las tres identidades canónicas.")
        if OutboundEmail.objects.exists():
            raise DemoIntegrityError("La cola de correo no quedó vacía.")
        if BusinessSignupRequest.objects.exists():
            raise DemoIntegrityError("Persisten solicitudes creadas durante la evaluación.")
        assert_no_evaluator_residue()

        fingerprint = demo_semantic_fingerprint()
        result = {
            "anchor_date": anchor_date.isoformat(),
            "boe_signature": after_boe,
            "boe_years": boe_years,
            "deleted_rows": sum(deleted.values()),
            "fingerprint": fingerprint,
            "run_id": run_id,
            "summary": summary,
        }
        # Segunda barrera: cubre conexiones que aparezcan después del preflight.
        # Los ACCESS EXCLUSIVE siguen retenidos hasta que finaliza atomic().
        validate_no_other_client_connections()
        DemoRefreshReceipt._base_manager.create(
            run_id=run_id,
            base_date=anchor_date,
            fingerprint=fingerprint,
        )
        return result

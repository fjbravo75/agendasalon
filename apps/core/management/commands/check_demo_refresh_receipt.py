from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from apps.core.demo_integrity import (
    DemoIntegrityError,
    demo_semantic_fingerprint,
    validate_refresh_run_id,
)
from apps.core.models import DemoRefreshReceipt


class Command(BaseCommand):
    help = "Comprueba por nonce si PostgreSQL confirmó una regeneración de la demo."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True, help="Nonce exacto de la ejecución.")

    def handle(self, *args, **options):
        try:
            run_id = validate_refresh_run_id(options["run_id"])
        except DemoIntegrityError as exc:
            raise CommandError(str(exc)) from exc

        try:
            receipt = DemoRefreshReceipt._base_manager.filter(run_id=run_id).first()
        except DatabaseError as exc:
            raise CommandError(
                "Estado indeterminado: PostgreSQL no pudo confirmar ni descartar el recibo."
            ) from exc
        if receipt is None:
            self.stdout.write(
                json.dumps(
                    {"committed": False, "run_id": run_id},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return

        try:
            current_fingerprint = demo_semantic_fingerprint()
        except DatabaseError as exc:
            raise CommandError(
                "Estado indeterminado: no se pudo verificar la huella actual de la demo."
            ) from exc
        if current_fingerprint != receipt.fingerprint:
            raise CommandError(
                "Estado indeterminado: el recibo existe, pero la huella actual no coincide."
            )

        self.stdout.write(
            json.dumps(
                {
                    "base_date": receipt.base_date.isoformat(),
                    "committed": True,
                    "completed_at": receipt.completed_at.isoformat(),
                    "fingerprint": receipt.fingerprint,
                    "run_id": receipt.run_id,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

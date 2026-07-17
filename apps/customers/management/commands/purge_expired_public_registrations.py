from django.core.management.base import BaseCommand, CommandError

from apps.businesses.models import Business
from apps.customers.services import (
    PUBLIC_REGISTRATION_RETENTION_SECONDS,
    purge_expired_public_registrations,
)


class Command(BaseCommand):
    help = "Purga de forma segura las altas públicas pendientes que hayan caducado."

    def add_arguments(self, parser):
        parser.add_argument(
            "--business-slug",
            help="Limita la purga a un negocio concreto.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=200,
            help=(
                "Máximo de altas pendientes purgadas o avanzadas de forma segura "
                "por negocio; las omitidas no consumen el lote (por defecto: 200)."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Informa de lo que se purgaría sin borrar datos.",
        )

    def handle(self, *args, **options):
        business_id = None
        business_slug = options.get("business_slug")
        if business_slug:
            business_id = (
                Business.objects.filter(slug=business_slug)
                .values_list("pk", flat=True)
                .first()
            )
            if business_id is None:
                raise CommandError(f"No existe el negocio {business_slug!r}.")

        batch_size = options["batch_size"]
        if batch_size <= 0:
            raise CommandError("--batch-size debe ser mayor que cero.")
        dry_run = options["dry_run"]
        result = purge_expired_public_registrations(
            business_id=business_id,
            batch_size=batch_size,
            dry_run=dry_run,
        )
        retention_hours = PUBLIC_REGISTRATION_RETENTION_SECONDS // (60 * 60)
        mode = "simulación" if dry_run else "purga"
        self.stdout.write(
            self.style.SUCCESS(
                "Altas públicas pendientes revisadas "
                f"(caducidad lógica: {retention_hours} h; modo: {mode}; "
                f"lote útil: {batch_size}): "
                f"{result.candidates} candidatas, {result.eligible} purgables, "
                f"{result.purged} purgadas y {result.skipped} conservadas por seguridad."
            )
        )

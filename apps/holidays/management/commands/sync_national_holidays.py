import requests
from django.core.management.base import BaseCommand, CommandError

from apps.holidays.services import BoeSyncError, sync_boe_national_holidays


class Command(BaseCommand):
    help = "Sincroniza desde BOE los festivos nacionales comunes para un año."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int, required=True, help="Año que se desea importar.")

    def handle(self, *args, **options):
        target_year = options["year"]
        if not 2000 <= target_year <= 2100:
            raise CommandError("El año debe estar entre 2000 y 2100.")

        try:
            result = sync_boe_national_holidays(target_year)
        except (requests.RequestException, BoeSyncError) as error:
            raise CommandError(str(error)) from error

        run = result.run
        self.stdout.write(f"{result.resolution.identifier}: {result.resolution.title}")
        self.stdout.write(
            self.style.SUCCESS(
                "Sincronización completada: "
                f"cargados={run.items_loaded}, "
                f"creados={run.items_created}, "
                f"actualizados={run.items_updated}, "
                f"retirados={run.items_removed}, "
                f"conservados={run.items_skipped}, "
                f"citas_afectadas={run.affected_appointments}."
            )
        )

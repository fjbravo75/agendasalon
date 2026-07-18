from django.core.management.base import BaseCommand, CommandError

from apps.core.demo_refresh_requests import (
    DemoRefreshFinalizationError,
    DemoRefreshRequestUnavailable,
    finalize_demo_refresh,
)


class Command(BaseCommand):
    help = "Cierra una petición manual con la salida y el recibo del orquestador root."

    def add_arguments(self, parser):
        parser.add_argument("--request-id", required=True)
        parser.add_argument(
            "--result",
            required=True,
            choices=("completed", "failed"),
        )
        parser.add_argument("--failure-code", default="")

    def handle(self, *args, **options):
        succeeded = options["result"] == "completed"
        try:
            refresh_request = finalize_demo_refresh(
                public_id=options["request_id"],
                succeeded=succeeded,
                failure_code=options["failure_code"],
            )
        except DemoRefreshRequestUnavailable as exc:
            raise CommandError("La regeneración manual no está habilitada.") from exc
        except DemoRefreshFinalizationError as exc:
            raise CommandError(f"No se pudo cerrar la petición: {exc}.") from exc
        self.stdout.write(
            f"FINALIZED|{refresh_request.status}|{refresh_request.public_id}"
        )

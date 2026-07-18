from django.core.management.base import BaseCommand, CommandError

from apps.core.demo_refresh_requests import (
    DemoRefreshRequestUnavailable,
    claim_pending_demo_refresh,
)


class Command(BaseCommand):
    help = "Reclama una petición manual pendiente para el despachador root estrecho."

    def handle(self, *args, **options):
        try:
            claim = claim_pending_demo_refresh()
        except DemoRefreshRequestUnavailable as exc:
            raise CommandError("La regeneración manual no está habilitada.") from exc
        if claim is None:
            self.stdout.write("IDLE")
            return
        refresh_request = claim.refresh_request
        marker = "RECOVER" if claim.recovery_required else "CLAIMED"
        self.stdout.write(
            f"{marker}|{refresh_request.public_id}|{refresh_request.base_date.isoformat()}"
        )

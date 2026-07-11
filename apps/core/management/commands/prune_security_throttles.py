from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import SecurityThrottle


class Command(BaseCommand):
    help = "Elimina contadores de seguridad inactivos fuera del periodo de retención."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Días de inactividad que se conservan (30 por defecto).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days debe ser un número entero mayor que cero.")
        cutoff = timezone.now() - timedelta(days=days)
        deleted, _ = SecurityThrottle.objects.filter(last_attempt_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(f"Contadores eliminados: {deleted}."))

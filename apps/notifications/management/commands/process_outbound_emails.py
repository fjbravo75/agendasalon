from django.core.management.base import BaseCommand

from apps.notifications.services import dispatch_due_emails


class Command(BaseCommand):
    help = "Envia los correos transaccionales pendientes cuya hora ya ha llegado."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args, **options):
        delivered = dispatch_due_emails(limit=max(1, options["limit"]))
        sent = sum(email.status == email.Status.SENT for email in delivered)
        failed = sum(email.status in {email.Status.PENDING, email.Status.FAILED} for email in delivered)
        self.stdout.write(self.style.SUCCESS(f"Procesados: {len(delivered)}. Enviados: {sent}. Pendientes o fallidos: {failed}."))

import uuid
from datetime import timedelta

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def initialize_delivery_state(apps, schema_editor):
    outbound_email = apps.get_model("notifications", "OutboundEmail")
    expired_at = timezone.now() - timedelta(seconds=1)
    for email in outbound_email.objects.all().only("pk", "status").iterator():
        updates = {"delivery_reference": uuid.uuid4()}
        if email.status == "processing":
            # Las filas anteriores a esta migracion no tenian lease. Se les
            # asigna uno ya vencido para que el worker pueda recuperarlas sin
            # cambiar silenciosamente su estado durante la migracion.
            updates.update(
                lease_token=uuid.uuid4(),
                lease_expires_at=expired_at,
            )
        outbound_email.objects.filter(pk=email.pk).update(**updates)


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0003_alter_outboundemail_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="outboundemail",
            name="delivery_reference",
            field=models.UUIDField(
                "identificador del aviso",
                blank=True,
                editable=False,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="outboundemail",
            name="lease_expires_at",
            field=models.DateTimeField(
                "procesamiento reservado hasta",
                blank=True,
                editable=False,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="outboundemail",
            name="lease_token",
            field=models.UUIDField(
                "identificador del procesamiento",
                blank=True,
                editable=False,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="outboundemail",
            name="cancellation_requested_at",
            field=models.DateTimeField(
                "cancelación solicitada el",
                blank=True,
                editable=False,
                null=True,
            ),
        ),
        migrations.RunPython(initialize_delivery_state, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="outboundemail",
            name="delivery_reference",
            field=models.UUIDField(
                "identificador del aviso",
                default=uuid.uuid4,
                editable=False,
                db_index=True,
            ),
        ),
        migrations.AddIndex(
            model_name="outboundemail",
            index=models.Index(
                fields=["status", "lease_expires_at"],
                name="email_status_lease_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="outboundemail",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        status="processing",
                        lease_token__isnull=False,
                        lease_expires_at__isnull=False,
                    )
                    | (
                        ~Q(status="processing")
                        & Q(lease_token__isnull=True)
                        & Q(lease_expires_at__isnull=True)
                    )
                ),
                name="email_processing_lease_state",
            ),
        ),
    ]

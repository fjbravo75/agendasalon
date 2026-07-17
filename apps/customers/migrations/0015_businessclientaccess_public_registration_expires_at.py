from datetime import timedelta

from django.db import migrations, models


PUBLIC_REGISTRATION_RETENTION = timedelta(hours=48)


def backfill_pending_public_registration_expiry(apps, schema_editor):
    # La línea P1 aceptada parte de cero accesos legacy sin verificar asociados
    # a fichas de origen "other". Este backfill es defensivo para instalaciones
    # que no compartan esa fotografía; su precondición y revisión previa están
    # documentadas en OPERACION_PRODUCCION.
    client_access = apps.get_model("customers", "BusinessClientAccess")
    pending_accesses = client_access.objects.using(schema_editor.connection.alias).filter(
        is_pending_public_registration=True,
        email_verified_at__isnull=True,
        public_registration_expires_at__isnull=True,
    )
    batch = []
    for access in pending_accesses.iterator(chunk_size=500):
        access.public_registration_expires_at = (
            access.created_at + PUBLIC_REGISTRATION_RETENTION
        )
        batch.append(access)
        if len(batch) == 500:
            client_access.objects.using(schema_editor.connection.alias).bulk_update(
                batch,
                ["public_registration_expires_at"],
            )
            batch.clear()
    if batch:
        client_access.objects.using(schema_editor.connection.alias).bulk_update(
            batch,
            ["public_registration_expires_at"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0014_sync_business_client_email_from_access"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessclientaccess",
            name="public_registration_expires_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name="caducidad del alta pública",
            ),
        ),
        migrations.RunPython(
            backfill_pending_public_registration_expiry,
            migrations.RunPython.noop,
        ),
    ]

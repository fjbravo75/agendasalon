from django.contrib.auth.hashers import make_password
from django.db import migrations, models


def secure_legacy_public_registrations(apps, schema_editor):
    """Move legacy unverified public sign-ups into the pending state."""

    database_alias = schema_editor.connection.alias
    business_client = apps.get_model("customers", "BusinessClient")
    client_access = apps.get_model("customers", "BusinessClientAccess")
    legacy_accesses = client_access.objects.using(database_alias).filter(
        email_verified_at__isnull=True,
        business_client__source="other",
    )
    client_ids = list(legacy_accesses.values_list("business_client_id", flat=True))
    if not client_ids:
        return

    business_client.objects.using(database_alias).filter(pk__in=client_ids).update(
        is_active=False
    )
    legacy_accesses.update(
        is_pending_public_registration=True,
        password_hash=make_password(None),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0012_limit_client_identity_to_professional_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessclientaccess",
            name="is_pending_public_registration",
            field=models.BooleanField(
                default=False,
                verbose_name="alta pública pendiente de verificar",
            ),
        ),
        migrations.RunPython(
            secure_legacy_public_registrations,
            migrations.RunPython.noop,
        ),
    ]

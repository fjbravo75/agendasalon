from django.db import migrations


def sync_business_client_email_from_access(apps, schema_editor):
    Access = apps.get_model("customers", "BusinessClientAccess")
    Client = apps.get_model("customers", "BusinessClient")
    db_alias = schema_editor.connection.alias

    verified_accesses = (
        Access.objects.using(db_alias)
        .filter(
            email_verified_at__isnull=False,
        )
        .exclude(email="")
    )
    for access in verified_accesses.only("business_client_id", "email").iterator():
        canonical_email = (access.email or "").strip()
        if not canonical_email:
            continue
        (
            Client.objects.using(db_alias)
            .filter(pk=access.business_client_id)
            .exclude(email=canonical_email)
            .update(email=canonical_email)
        )


def preserve_current_email_on_reverse(apps, schema_editor):
    # La divergencia anterior no es reconstruible sin guardar una copia histórica.
    # El rollback conserva por ello el último valor canónico aplicado.
    return None


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0013_businessclientaccess_pending_public_registration"),
    ]

    operations = [
        migrations.RunPython(
            sync_business_client_email_from_access,
            preserve_current_email_on_reverse,
        ),
    ]

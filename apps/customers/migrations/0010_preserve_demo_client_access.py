from django.db import migrations
from django.utils import timezone


DEMO_BUSINESS_SLUGS = ("peluqueria-mari", "barberia-norte")


def preserve_demo_client_access(apps, schema_editor):
    Access = apps.get_model("customers", "BusinessClientAccess")
    for access in (
        Access.objects.select_related("business", "business_client")
        .filter(business__slug__in=DEMO_BUSINESS_SLUGS, is_active=True)
        .iterator()
    ):
        email = (access.email or "").strip()
        if not email:
            email = f"cliente{access.business_client_id}@agendasalon.local"
            access.email = email
        access.email_normalized = email.lower()
        access.email_verified_at = timezone.now()
        access.save(
            update_fields=[
                "email",
                "email_normalized",
                "email_verified_at",
                "updated_at",
            ]
        )
        client = access.business_client
        if not (client.email or "").strip():
            client.email = email
            client.save(update_fields=["email", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0009_businessclientaccess_email_and_more"),
    ]

    operations = [
        migrations.RunPython(preserve_demo_client_access, migrations.RunPython.noop),
    ]

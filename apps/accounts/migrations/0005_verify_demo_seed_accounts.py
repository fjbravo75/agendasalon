from django.db import migrations
from django.utils import timezone


DEMO_ACCOUNT_EMAILS = (
    "admin@agendasalon.local",
    "mari@agendasalon.local",
    "equipo@barberianorte.local",
)


def verify_demo_seed_accounts(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(
        email_normalized__in=DEMO_ACCOUNT_EMAILS,
        is_active=True,
    ).update(
        email_verified_at=timezone.now(),
        email_verification_required=False,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_user_email_normalized_and_more"),
    ]

    operations = [
        migrations.RunPython(verify_demo_seed_accounts, migrations.RunPython.noop),
    ]

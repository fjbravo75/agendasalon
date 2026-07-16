from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0011_remove_businessclientaccess_unique_business_client_access_phone"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="businessclient",
            name="unique_active_business_client_identity",
        ),
        migrations.AddConstraint(
            model_name="businessclient",
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(is_active=True, source="professional")
                    & ~models.Q(phone_normalized="")
                ),
                fields=("business", "phone_normalized", "full_name_normalized"),
                name="unique_active_professional_client_identity",
            ),
        ),
    ]

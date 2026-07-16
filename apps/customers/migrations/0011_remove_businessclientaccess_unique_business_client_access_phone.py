from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("customers", "0010_preserve_demo_client_access"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="businessclientaccess",
            name="unique_business_client_access_phone",
        ),
    ]

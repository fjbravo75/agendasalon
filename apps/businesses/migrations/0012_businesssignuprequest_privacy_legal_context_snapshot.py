from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0011_businesssignuprequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="businesssignuprequest",
            name="privacy_legal_context_snapshot",
            field=models.JSONField(
                default=dict,
                verbose_name="contexto legal de privacidad mostrado",
            ),
        ),
    ]

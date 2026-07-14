from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_alter_user_normalized_phone_alter_user_phone"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="password_change_required",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Obliga a sustituir una contraseña temporal antes de usar "
                    "AgendaSalon."
                ),
                verbose_name="cambio de contraseña obligatorio",
            ),
        ),
    ]

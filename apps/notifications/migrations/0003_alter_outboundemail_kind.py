from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0002_outboundemail"),
    ]

    operations = [
        migrations.AlterField(
            model_name="outboundemail",
            name="kind",
            field=models.CharField(
                choices=[
                    ("professional_activation", "Activación profesional"),
                    ("professional_email_verification", "Verificación profesional"),
                    ("client_email_verification", "Verificación cliente"),
                    ("client_password_reset", "Recuperación de contraseña cliente"),
                    ("appointment_confirmation", "Confirmación de cita"),
                    ("appointment_reminder", "Recordatorio de cita"),
                ],
                max_length=48,
                verbose_name="tipo",
            ),
        ),
    ]

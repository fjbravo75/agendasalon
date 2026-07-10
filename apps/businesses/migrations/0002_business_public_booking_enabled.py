from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="business",
            name="public_booking_enabled",
            field=models.BooleanField(default=True, verbose_name="reserva pública activa"),
        ),
    ]

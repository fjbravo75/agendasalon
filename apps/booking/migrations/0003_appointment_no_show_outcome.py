import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0002_alter_appointment_manual_channel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="appointment",
            name="status",
            field=models.CharField(
                choices=[
                    ("confirmada", "Confirmada"),
                    ("cancelada", "Cancelada"),
                    ("completada", "Atendida"),
                    ("no_presentada", "No se presentó"),
                ],
                default="confirmada",
                max_length=20,
                verbose_name="estado",
            ),
        ),
        migrations.AddField(
            model_name="appointment",
            name="no_show_marked_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="fecha de ausencia"),
        ),
        migrations.AddField(
            model_name="appointment",
            name="no_show_marked_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="no_show_appointments",
                to=settings.AUTH_USER_MODEL,
                verbose_name="ausencia marcada por",
            ),
        ),
    ]

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("booking", "0007_appointment_public_confirmation_reference"),
        ("holidays", "0004_normalize_future_holiday_sync_runs"),
    ]

    operations = [
        migrations.CreateModel(
            name="HolidayAppointmentReview",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("holiday_date", models.DateField(verbose_name="fecha festiva")),
                (
                    "holiday_name",
                    models.CharField(max_length=180, verbose_name="festivo registrado"),
                ),
                ("reviewed_at", models.DateTimeField(verbose_name="fecha de revisión")),
                (
                    "appointment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="national_holiday_reviews",
                        to="booking.appointment",
                        verbose_name="cita",
                    ),
                ),
                (
                    "holiday",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="appointment_reviews",
                        to="holidays.officialholiday",
                        verbose_name="festivo oficial",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="holiday_appointment_reviews",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="revisado por",
                    ),
                ),
            ],
            options={
                "verbose_name": "revisión de cita en festivo",
                "verbose_name_plural": "revisiones de citas en festivo",
                "ordering": ["-reviewed_at", "-pk"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("appointment", "holiday_date"),
                        name="unique_holiday_review_per_appointment_date",
                    ),
                    models.CheckConstraint(
                        condition=~models.Q(holiday_name=""),
                        name="holiday_review_name_not_empty",
                    ),
                ],
            },
        ),
    ]

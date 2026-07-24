import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0015_alter_businessactivityevent_event_type"),
        ("customers", "0015_businessclientaccess_public_registration_expires_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="businessclient",
            name="merge_review_dismissed_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="coincidencia descartada el",
            ),
        ),
        migrations.AddField(
            model_name="businessclient",
            name="merge_review_dismissed_by",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dismissed_client_merge_reviews",
                to=settings.AUTH_USER_MODEL,
                verbose_name="coincidencia descartada por",
            ),
        ),
        migrations.AddField(
            model_name="businessclient",
            name="merge_review_dismissed_fingerprint",
            field=models.CharField(
                blank=True,
                editable=False,
                max_length=64,
                verbose_name="coincidencia descartada",
            ),
        ),
        migrations.AddField(
            model_name="businessclient",
            name="merged_at",
            field=models.DateTimeField(
                blank=True,
                editable=False,
                null=True,
                verbose_name="unificada el",
            ),
        ),
        migrations.AddField(
            model_name="businessclient",
            name="merged_by",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="merged_client_records",
                to=settings.AUTH_USER_MODEL,
                verbose_name="unificada por",
            ),
        ),
        migrations.AddField(
            model_name="businessclient",
            name="merged_into",
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="merged_records",
                to="customers.businessclient",
                verbose_name="ficha resultante",
            ),
        ),
        migrations.AddConstraint(
            model_name="businessclient",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(merged_into__isnull=True, merged_at__isnull=True)
                    | models.Q(
                        merged_into__isnull=False,
                        merged_at__isnull=False,
                        is_active=False,
                    )
                ),
                name="client_merge_state_consistent",
            ),
        ),
    ]

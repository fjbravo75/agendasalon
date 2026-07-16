from django.db import migrations
from django.utils import timezone


def normalize_future_holiday_sync_runs(apps, schema_editor):
    HolidaySyncRun = apps.get_model("holidays", "HolidaySyncRun")
    now = timezone.now()
    HolidaySyncRun.objects.filter(started_at__gt=now).update(
        started_at=now,
        finished_at=now,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("holidays", "0003_remove_officialholiday_unique_official_holiday_and_more"),
    ]

    operations = [
        migrations.RunPython(normalize_future_holiday_sync_runs, migrations.RunPython.noop),
    ]

from django.db import migrations


SENSITIVE_CHANGE_KEYS = {"requested_for", "requested_by"}


def redact_public_booking_activity(apps, schema_editor):
    BusinessActivityEvent = apps.get_model("businesses", "BusinessActivityEvent")
    queryset = BusinessActivityEvent.objects.filter(
        event_type="appointment_created",
        origin="web_publica",
    )
    pending_updates = []
    for event in queryset.iterator(chunk_size=500):
        changes = event.changes if isinstance(event.changes, dict) else {}
        event.actor_label = "Cliente online"
        event.changes = {
            key: value
            for key, value in changes.items()
            if key not in SENSITIVE_CHANGE_KEYS
        }
        pending_updates.append(event)
        if len(pending_updates) >= 500:
            BusinessActivityEvent.objects.bulk_update(
                pending_updates,
                ["actor_label", "changes"],
            )
            pending_updates.clear()
    if pending_updates:
        BusinessActivityEvent.objects.bulk_update(
            pending_updates,
            ["actor_label", "changes"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("businesses", "0008_alter_businessactivityevent_event_type"),
    ]

    operations = [
        migrations.RunPython(
            redact_public_booking_activity,
            reverse_code=migrations.RunPython.noop,
        ),
    ]

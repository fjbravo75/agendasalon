from django.core.exceptions import ValidationError

from apps.businesses.models import Business, BusinessActivityEvent


def record_business_activity(
    *,
    business,
    category,
    event_type,
    origin,
    summary,
    actor=None,
    actor_type=None,
    actor_label=None,
    entity=None,
    entity_type="",
    changes=None,
    event_at=None,
):
    summary = (summary or "").strip()
    if not summary:
        raise ValidationError("El movimiento debe incluir un resumen.")

    if actor_type is None:
        if actor is None:
            actor_type = BusinessActivityEvent.ActorType.SYSTEM
        elif actor.is_superuser:
            actor_type = BusinessActivityEvent.ActorType.SUPERADMIN
        else:
            actor_type = BusinessActivityEvent.ActorType.PROFESSIONAL

    if actor_label is None:
        if actor is None:
            actor_label = "Sistema"
        else:
            actor_label = actor.full_name or actor.normalized_phone

    event = BusinessActivityEvent(
        business=business,
        actor_user=actor,
        actor_type=actor_type,
        actor_label=actor_label,
        category=category,
        event_type=event_type,
        origin=origin,
        summary=summary,
        entity_type=entity_type,
        entity_id=getattr(entity, "pk", None),
        changes=changes or {},
    )
    event.full_clean()
    event.save()
    if event_at is not None:
        BusinessActivityEvent.objects.filter(pk=event.pk).update(created_at=event_at)
        event.created_at = event_at
    Business.objects.filter(pk=business.pk).update(last_activity_at=event.created_at)
    return event

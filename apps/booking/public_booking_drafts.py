from datetime import timedelta

from django.utils import timezone
from django.utils.dateparse import parse_datetime


PUBLIC_BOOKING_DRAFTS_SESSION_KEY = "public_booking_drafts"
PUBLIC_BOOKING_DRAFT_TTL = timedelta(minutes=30)


def save_public_booking_draft(request, business, cleaned_data):
    drafts = dict(request.session.get(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, {}))
    drafts[str(business.id)] = {
        "service_ids": [service.id for service in cleaned_data["services"]],
        "target_date": cleaned_data["target_date"].isoformat(),
        "selected_work_line_id": cleaned_data["selected_work_line_id"],
        "selected_starts_at": cleaned_data["selected_starts_at"].isoformat(),
        "saved_at": timezone.now().isoformat(),
    }
    request.session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY] = drafts
    return drafts[str(business.id)]


def get_public_booking_draft(request, business):
    drafts = request.session.get(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, {})
    draft = drafts.get(str(business.id))
    if not isinstance(draft, dict):
        return None

    saved_at = parse_datetime(draft.get("saved_at", ""))
    if saved_at is None:
        clear_public_booking_draft(request, business)
        return None
    if timezone.is_naive(saved_at):
        saved_at = timezone.make_aware(saved_at)
    if saved_at < timezone.now() - PUBLIC_BOOKING_DRAFT_TTL:
        clear_public_booking_draft(request, business)
        return None

    required_fields = {
        "service_ids",
        "target_date",
        "selected_work_line_id",
        "selected_starts_at",
    }
    if not required_fields.issubset(draft):
        clear_public_booking_draft(request, business)
        return None
    return dict(draft)


def clear_public_booking_draft(request, business):
    drafts = dict(request.session.get(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, {}))
    if drafts.pop(str(business.id), None) is None:
        return
    if drafts:
        request.session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY] = drafts
    else:
        request.session.pop(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, None)


def public_booking_draft_form_data(draft):
    return {
        "services": draft["service_ids"],
        "target_date": draft["target_date"],
        "selected_work_line_id": draft["selected_work_line_id"],
        "selected_starts_at": draft["selected_starts_at"],
    }

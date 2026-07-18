from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.utils import timezone

from apps.booking.models import Appointment
from apps.businesses.models import Business, BusinessSignupRequest
from apps.core.demo_integrity import CANONICAL_USER_PHONES
from apps.core.demo_scenario import APPOINTMENTS, CLIENTS
from apps.core.models import DemoRefreshReceipt, DemoRefreshRequest
from apps.customers.models import BusinessClient
from apps.notifications.models import OutboundEmail


CANONICAL_BUSINESS_SLUGS = ("peluqueria-mari", "barberia-norte")


def demo_refresh_snapshot(*, now=None):
    now = now or timezone.now()
    noncompleted_request_statuses = (
        DemoRefreshRequest.Status.PENDING,
        DemoRefreshRequest.Status.PROCESSING,
        DemoRefreshRequest.Status.FAILED,
        DemoRefreshRequest.Status.CANCELLED,
    )
    active_request = DemoRefreshRequest.objects.filter(
        status__in=(
            DemoRefreshRequest.Status.PENDING,
            DemoRefreshRequest.Status.PROCESSING,
        )
    ).first()
    latest_request = DemoRefreshRequest.objects.first()
    noncompleted_manual_run_ids = tuple(
        str(public_id)
        for public_id in DemoRefreshRequest.objects.filter(
            status__in=noncompleted_request_statuses
        ).values_list("public_id", flat=True)
    )
    latest_receipt = (
        DemoRefreshReceipt.objects.exclude(
            manual_requests__status__in=noncompleted_request_statuses
        )
        .exclude(run_id__in=noncompleted_manual_run_ids)
        .first()
    )
    last_completed = DemoRefreshRequest.objects.filter(
        status=DemoRefreshRequest.Status.COMPLETED
    ).first()
    recommended_before = now - timedelta(
        days=int(settings.AGENDA_DEMO_REFRESH_RECOMMENDED_MAX_AGE_DAYS)
    )
    freshness_reference = latest_receipt.completed_at if latest_receipt else None

    User = get_user_model()
    counts = {
        "businesses": Business.objects.count(),
        "additional_businesses": Business.objects.exclude(
            slug__in=CANONICAL_BUSINESS_SLUGS
        ).count(),
        "appointments": Appointment.objects.count(),
        "clients": BusinessClient.objects.count(),
        "active_sessions": Session.objects.filter(expire_date__gt=now).count(),
        "additional_users": User._base_manager.exclude(
            normalized_phone__in=CANONICAL_USER_PHONES
        ).count(),
        "signup_requests": BusinessSignupRequest.objects.count(),
        "outbound_pending": OutboundEmail.objects.filter(
            status__in=(
                OutboundEmail.Status.PENDING,
                OutboundEmail.Status.PROCESSING,
            )
        ).count(),
    }
    return {
        "base_date": timezone.localdate(),
        "active_request": active_request,
        "latest_request": latest_request,
        "latest_receipt": latest_receipt,
        "last_completed": last_completed,
        "needs_attention": bool(
            freshness_reference is None or freshness_reference < recommended_before
            or (
                latest_request is not None
                and latest_request.status == DemoRefreshRequest.Status.FAILED
            )
        ),
        "has_mutable_changes": bool(
            counts["businesses"] != len(CANONICAL_BUSINESS_SLUGS)
            or counts["additional_businesses"]
            or counts["appointments"] != len(APPOINTMENTS)
            or counts["clients"] != len(CLIENTS)
            or counts["additional_users"]
            or counts["signup_requests"]
            or counts["outbound_pending"]
        ),
        "counts": counts,
    }

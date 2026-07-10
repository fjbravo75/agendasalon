from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_cookie

from apps.booking.models import Appointment
from apps.businesses.models import Business, BusinessActivityEvent
from apps.customers.models import BusinessClient


SUPERADMIN_DASHBOARD_API_VERSION = "1.0"
ACTIVITY_DAYS = 14


@login_required
@require_GET
@never_cache
@vary_on_cookie
def superadmin_dashboard_data(request):
    if not request.user.is_superuser:
        return _error_response(
            "superadmin_required",
            "Se necesita acceso de superadministración para consultar este panel.",
            status=403,
        )

    now = timezone.now()
    businesses = list(
        Business.objects.annotate(
            active_services_count=Count(
                "services",
                filter=Q(services__is_active=True),
                distinct=True,
            ),
            active_lines_count=Count(
                "work_lines",
                filter=Q(work_lines__is_active=True),
                distinct=True,
            ),
            active_rules_count=Count(
                "availability_rules",
                filter=Q(availability_rules__is_active=True),
                distinct=True,
            ),
            clients_total=Count(
                "clients",
                filter=Q(clients__is_active=True),
                distinct=True,
            ),
            appointments_total=Count("appointments", distinct=True),
            professionals_total=Count(
                "memberships",
                filter=Q(memberships__is_active=True),
                distinct=True,
            ),
            upcoming_confirmed_count=Count(
                "appointments",
                filter=Q(
                    appointments__status=Appointment.Status.CONFIRMED,
                    appointments__ends_at__gt=now,
                ),
                distinct=True,
            ),
            pending_closure_count=Count(
                "appointments",
                filter=Q(
                    appointments__status=Appointment.Status.CONFIRMED,
                    appointments__ends_at__lte=now,
                ),
                distinct=True,
            ),
        ).order_by("commercial_name", "pk")
    )
    business_payloads = [_business_payload(business) for business in businesses]

    status_counts = {
        item["status"]: item["total"]
        for item in Appointment.objects.values("status").annotate(total=Count("id"))
    }
    channel_counts = {
        item["manual_channel"]: item["total"]
        for item in Appointment.objects.values("manual_channel").annotate(total=Count("id"))
    }
    upcoming_confirmed_count = Appointment.objects.filter(
        status=Appointment.Status.CONFIRMED,
        ends_at__gt=now,
    ).count()
    pending_closure_count = sum(
        business["counts"]["pending_closure"] for business in business_payloads
    )

    return JsonResponse(
        {
            "schema_version": SUPERADMIN_DASHBOARD_API_VERSION,
            "generated_at": timezone.localtime(now).isoformat(),
            "summary": {
                "businesses_total": len(business_payloads),
                "businesses_active": sum(item["is_active"] for item in business_payloads),
                "businesses_inactive": sum(not item["is_active"] for item in business_payloads),
                "businesses_operational": sum(
                    item["health"]["code"] == "operational" for item in business_payloads
                ),
                "businesses_setup_pending": sum(
                    item["health"]["code"] == "setup_pending" for item in business_payloads
                ),
                "businesses_public_booking": sum(
                    item["is_active"] and item["public_booking_enabled"]
                    for item in business_payloads
                ),
                "businesses_with_pending_closure": sum(
                    item["counts"]["pending_closure"] > 0 for item in business_payloads
                ),
                "pending_closure_appointments": pending_closure_count,
                "professionals_active": sum(
                    item["counts"]["professionals"] for item in business_payloads
                ),
                "clients_total": BusinessClient.objects.count(),
                "appointments_total": Appointment.objects.count(),
            },
            "businesses": business_payloads,
            "recent_activity": _recent_activity_payload(),
            "activity_series": _activity_series_payload(),
            "appointment_statuses": [
                {"code": "upcoming", "label": "Confirmadas próximas", "value": upcoming_confirmed_count},
                {"code": "pending_closure", "label": "Pendientes de cierre", "value": pending_closure_count},
                {
                    "code": Appointment.Status.COMPLETED,
                    "label": "Atendidas",
                    "value": status_counts.get(Appointment.Status.COMPLETED, 0),
                },
                {
                    "code": Appointment.Status.NO_SHOW,
                    "label": "No presentadas",
                    "value": status_counts.get(Appointment.Status.NO_SHOW, 0),
                },
                {
                    "code": Appointment.Status.CANCELLED,
                    "label": "Canceladas",
                    "value": status_counts.get(Appointment.Status.CANCELLED, 0),
                },
            ],
            "appointment_channels": [
                _channel_payload(channel_counts, Appointment.ManualChannel.PUBLIC_WEB),
                _channel_payload(channel_counts, Appointment.ManualChannel.PHONE),
                _channel_payload(channel_counts, Appointment.ManualChannel.FRONT_DESK),
                _channel_payload(channel_counts, Appointment.ManualChannel.WHATSAPP),
                _channel_payload(channel_counts, Appointment.ManualChannel.EMAIL),
                _channel_payload(channel_counts, Appointment.ManualChannel.OTHER),
            ],
        }
    )


def _business_payload(business):
    missing_setup = []
    if not business.active_services_count:
        missing_setup.append("Servicios")
    if not business.active_lines_count:
        missing_setup.append("Líneas de trabajo")
    if not business.active_rules_count:
        missing_setup.append("Horario")
    if not business.professionals_total:
        missing_setup.append("Acceso profesional")

    if not business.is_active:
        health = {
            "code": "inactive",
            "label": "Pausado",
            "tone": "quiet",
            "detail": "El acceso profesional está pausado.",
            "missing_setup": missing_setup,
        }
    elif missing_setup:
        health = {
            "code": "setup_pending",
            "label": "Por configurar",
            "tone": "warning",
            "detail": f"Pendiente: {', '.join(missing_setup).lower()}.",
            "missing_setup": missing_setup,
        }
    else:
        health = {
            "code": "operational",
            "label": "Operativo",
            "tone": "ready",
            "detail": "Configuración básica completa.",
            "missing_setup": [],
        }

    return {
        "id": business.id,
        "name": business.commercial_name,
        "city": business.city or "Sin localidad indicada",
        "is_active": business.is_active,
        "public_booking_enabled": business.public_booking_enabled,
        "last_activity_at": _datetime_value(business.last_activity_at),
        "health": health,
        "counts": {
            "services": business.active_services_count,
            "work_lines": business.active_lines_count,
            "schedule_rules": business.active_rules_count,
            "professionals": business.professionals_total,
            "clients": business.clients_total,
            "appointments": business.appointments_total,
            "upcoming_confirmed": business.upcoming_confirmed_count,
            "pending_closure": business.pending_closure_count,
        },
        "urls": {
            "detail": reverse("businesses:superadmin_business_detail", args=[business.pk]),
        },
    }


def _recent_activity_payload():
    events = (
        BusinessActivityEvent.objects.select_related("business")
        .order_by("-id")[:8]
    )
    return [
        {
            "id": event.id,
            "created_at": _datetime_value(event.created_at),
            "business": {"id": event.business_id, "name": event.business.commercial_name},
            "category": event.category,
            "category_label": event.get_category_display(),
            "event_type": event.event_type,
            "event_label": event.get_event_type_display(),
            "origin": event.origin,
            "origin_label": event.get_origin_display(),
        }
        for event in events
    ]


def _activity_series_payload():
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=ACTIVITY_DAYS - 1)
    totals = {
        item["day"]: item["total"]
        for item in (
            BusinessActivityEvent.objects.filter(created_at__date__gte=start_date)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(total=Count("id"))
            .order_by("day")
        )
    }
    return [
        {
            "date": (start_date + timedelta(days=offset)).isoformat(),
            "value": totals.get(start_date + timedelta(days=offset), 0),
        }
        for offset in range(ACTIVITY_DAYS)
    ]


def _channel_payload(channel_counts, channel):
    labels = {
        Appointment.ManualChannel.EMAIL: "Correo electrónico",
        Appointment.ManualChannel.OTHER: "Otro canal",
    }
    return {
        "code": channel,
        "label": labels.get(channel, Appointment.ManualChannel(channel).label),
        "value": channel_counts.get(channel, 0),
    }


def _datetime_value(value):
    return timezone.localtime(value).isoformat() if value else None


def _error_response(code, message, *, status):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from django.views.decorators.vary import vary_on_cookie

from apps.booking.models import Appointment, AvailabilityRule, Service, WorkLine
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.customers.models import BusinessClient
from apps.legal.models import BusinessLegalProfile, LegalAcceptance, LegalDocument


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
    businesses = list(_dashboard_business_queryset())
    _attach_business_counts(businesses, now=now)
    business_payloads = [_business_payload(business) for business in businesses]

    status_counts = {}
    channel_counts = {}
    for item in (
        Appointment.objects.values("status", "manual_channel").annotate(total=Count("id"))
    ):
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + item["total"]
        channel_counts[item["manual_channel"]] = (
            channel_counts.get(item["manual_channel"], 0) + item["total"]
        )
    upcoming_confirmed_count = sum(
        business["counts"]["upcoming_confirmed"] for business in business_payloads
    )
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
                "clients_total": sum(business._all_clients_count for business in businesses),
                "appointments_total": sum(
                    business.appointments_total for business in businesses
                ),
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


def _attach_business_counts(businesses, *, now):
    """Carga métricas por relación sin formar un producto cartesiano gigante."""

    business_ids = [business.pk for business in businesses]
    if not business_ids:
        return

    count_maps = {
        "active_services_count": _grouped_counts(
            Service.objects.filter(business_id__in=business_ids, is_active=True)
        ),
        "active_lines_count": _grouped_counts(
            WorkLine.objects.filter(business_id__in=business_ids, is_active=True)
        ),
        "active_rules_count": _grouped_counts(
            AvailabilityRule.objects.filter(business_id__in=business_ids, is_active=True)
        ),
        "professionals_total": _grouped_counts(
            BusinessMembership.objects.filter(business_id__in=business_ids, is_active=True)
        ),
    }
    client_counts = {
        row["business_id"]: row
        for row in (
            BusinessClient.objects.filter(business_id__in=business_ids)
            .values("business_id")
            .annotate(
                clients_total=Count("id", filter=Q(is_active=True)),
                all_clients_count=Count("id"),
            )
        )
    }
    appointment_counts = {
        row["business_id"]: row
        for row in (
            Appointment.objects.filter(business_id__in=business_ids)
            .values("business_id")
            .annotate(
                appointments_total=Count("id"),
                upcoming_confirmed_count=Count(
                    "id",
                    filter=Q(status=Appointment.Status.CONFIRMED, ends_at__gt=now),
                ),
                pending_closure_count=Count(
                    "id",
                    filter=Q(status=Appointment.Status.CONFIRMED, ends_at__lte=now),
                ),
            )
        )
    }

    for business in businesses:
        for attribute, counts in count_maps.items():
            setattr(business, attribute, counts.get(business.pk, 0))
        client_row = client_counts.get(business.pk, {})
        business.clients_total = client_row.get("clients_total", 0)
        business._all_clients_count = client_row.get("all_clients_count", 0)
        appointment_row = appointment_counts.get(business.pk, {})
        for attribute in (
            "appointments_total",
            "upcoming_confirmed_count",
            "pending_closure_count",
        ):
            setattr(business, attribute, appointment_row.get(attribute, 0))


def _dashboard_business_queryset():
    profile_complete = (
        BusinessLegalProfile.objects.filter(business_id=OuterRef("pk"))
        .exclude(legal_name="")
        .exclude(tax_identifier="")
        .exclude(registered_address="")
        .exclude(privacy_email="")
        .exclude(retention_criteria="")
    )
    acceptance_base = LegalAcceptance.objects.filter(
        business_id=OuterRef("pk"),
        actor_user__isnull=False,
        context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
        document__is_active=True,
    )
    return (
        Business.objects.annotate(
            legal_profile_complete=Exists(profile_complete),
            legal_terms_current=Exists(
                acceptance_base.filter(
                    document__kind=LegalDocument.Kind.TERMS,
                    action=LegalAcceptance.Action.ACCEPTED,
                )
            ),
            legal_privacy_current=Exists(
                acceptance_base.filter(
                    document__kind=LegalDocument.Kind.PLATFORM_PRIVACY,
                    action=LegalAcceptance.Action.ACKNOWLEDGED,
                )
            ),
            legal_processing_current=Exists(
                acceptance_base.filter(
                    document__kind=LegalDocument.Kind.DATA_PROCESSING,
                    action=LegalAcceptance.Action.ACCEPTED,
                    authority_declared=True,
                )
            ),
            legal_latest_acceptance_at=Subquery(
                acceptance_base.order_by("-accepted_at").values("accepted_at")[:1]
            ),
        )
        .order_by("commercial_name", "pk")
    )


def _grouped_counts(queryset):
    return {
        row["business_id"]: row["total"]
        for row in queryset.values("business_id").annotate(total=Count("id"))
    }


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
    if not business.legal_compliance_enabled:
        legal_status = {
            "is_current": True,
            "status": "disabled",
            "label": "Control legal no requerido",
            "latest_acceptance_at": None,
        }
    else:
        legal_is_current = all(
            (
                business.legal_profile_complete,
                business.legal_terms_current,
                business.legal_privacy_current,
                business.legal_processing_current,
            )
        )
        legal_status = {
            "is_current": legal_is_current,
            "status": "current" if legal_is_current else "pending_documents",
            "label": "Documentación vigente" if legal_is_current else "Documentación pendiente",
            "latest_acceptance_at": business.legal_latest_acceptance_at,
        }
    if not legal_status["is_current"]:
        missing_setup.append("Documentación legal")

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
        "legal": {
            "is_current": legal_status["is_current"],
            "status": legal_status["status"],
            "label": legal_status["label"],
            "latest_acceptance_at": _datetime_value(legal_status["latest_acceptance_at"]),
        },
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

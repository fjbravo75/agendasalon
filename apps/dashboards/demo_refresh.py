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
DEMO_REFRESH_FAILURE_DETAILS = {
    "runtime_recovery_required": (
        "Los datos llegaron a generar un recibo, pero el servidor no pudo acreditar "
        "que todos los servicios quedaran recuperados con seguridad."
    ),
    "dispatcher_interrupted": (
        "El despachador se interrumpió antes de obtener un resultado verificable."
    ),
    "orchestrator_failed": (
        "El proceso protegido no completó todas las comprobaciones necesarias."
    ),
}


def _status_presentation(latest_request):
    if latest_request is None:
        return {
            "key": "available",
            "tone": "neutral",
            "badge": "Disponible",
            "headline": "Sin solicitudes en curso",
            "detail": (
                "No hay una regeneración pendiente. Puedes revisar el alcance antes "
                "de autorizar una nueva solicitud."
            ),
            "guidance": (
                "La operación solo comienza después de confirmar la contraseña, la "
                "frase de seguridad y el alcance destructivo."
            ),
        }

    presentations = {
        DemoRefreshRequest.Status.PENDING: {
            "key": "pending",
            "tone": "warning",
            "badge": "Solicitada",
            "headline": "Solicitud recibida",
            "detail": (
                "La petición está registrada y espera a que el servidor inicie el "
                "proceso protegido."
            ),
            "guidance": (
                "No envíes otra solicitud. Actualiza esta página para consultar el "
                "cambio de estado."
            ),
        },
        DemoRefreshRequest.Status.PROCESSING: {
            "key": "processing",
            "tone": "warning",
            "badge": "En curso",
            "headline": "Regeneración en curso",
            "detail": (
                "El servidor está reconstruyendo y verificando el escenario. La "
                "aplicación puede dejar de responder durante unos minutos."
            ),
            "guidance": (
                "No cierres ni reinicies servicios manualmente. Actualiza esta página "
                "para consultar el resultado."
            ),
        },
        DemoRefreshRequest.Status.COMPLETED: {
            "key": "completed",
            "tone": "success",
            "badge": "Completada",
            "headline": "Última regeneración completada",
            "detail": (
                "El servidor terminó el proceso y vinculó un recibo que acredita el "
                "escenario verificado."
            ),
            "guidance": (
                "Puedes consultar debajo la fecha, el identificador y la huella del "
                "recibo técnico."
            ),
        },
        DemoRefreshRequest.Status.FAILED: {
            "key": "failed",
            "tone": "danger",
            "badge": "Incidencia",
            "headline": "La última regeneración necesita revisión",
            "detail": DEMO_REFRESH_FAILURE_DETAILS.get(
                latest_request.failure_code,
                "El servidor no pudo completar todas las comprobaciones de la operación.",
            ),
            "guidance": (
                "Revisa la referencia técnica antes de solicitar otra regeneración."
            ),
        },
        DemoRefreshRequest.Status.CANCELLED: {
            "key": "cancelled",
            "tone": "neutral",
            "badge": "Cancelada",
            "headline": "La última solicitud fue cancelada",
            "detail": (
                "La operación quedó cerrada sin declarar una regeneración completada."
            ),
            "guidance": (
                "Puedes revisar el alcance y autorizar una nueva solicitud cuando sea "
                "necesario."
            ),
        },
    }
    return presentations[latest_request.status]


def demo_refresh_snapshot(*, now=None):
    now = now or timezone.now()
    noncompleted_request_statuses = (
        DemoRefreshRequest.Status.PENDING,
        DemoRefreshRequest.Status.PROCESSING,
        DemoRefreshRequest.Status.FAILED,
        DemoRefreshRequest.Status.CANCELLED,
    )
    request_queryset = DemoRefreshRequest.objects.select_related(
        "requested_by",
        "receipt",
    )
    active_request = request_queryset.filter(
        status__in=(
            DemoRefreshRequest.Status.PENDING,
            DemoRefreshRequest.Status.PROCESSING,
        )
    ).first()
    latest_request = request_queryset.first()
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
    display_receipt = (
        latest_request.receipt
        if latest_request is not None and latest_request.receipt_id
        else latest_receipt
    )

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
        "display_receipt": display_receipt,
        "display_receipt_is_current": bool(
            latest_request is not None
            and latest_request.receipt_id
            and display_receipt is not None
            and latest_request.receipt_id == display_receipt.pk
        ),
        "last_completed": last_completed,
        "status": _status_presentation(latest_request),
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

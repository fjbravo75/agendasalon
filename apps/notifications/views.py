from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.businesses.activity import record_business_activity
from apps.businesses.models import (
    BusinessActivityEvent,
    BusinessSignupRequest,
    PlatformActivityEvent,
    PlatformSettings,
)
from apps.businesses.services import get_primary_business_for_user
from apps.core.models import DemoRefreshReceipt
from apps.core.features import (
    operational_notification_delivery_enabled,
    operational_notifications_enabled,
)
from apps.core.security_throttle import (
    ThrottleLimit,
    request_ip,
    reserve_throttle_attempts,
    settle_successful_throttle,
)
from apps.dashboards.models import BackupExecution
from apps.notifications.forms import PlatformNotificationSettingsForm
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    operational_email_target_from_token,
    queue_operational_email_verification_safely,
    queue_operational_test,
    verify_operational_email,
)


def _require_feature():
    if not operational_notifications_enabled():
        raise Http404


def _require_superadmin(request):
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied


def _reserve_email_action(request, *, action, limit=5, target_email=""):
    limits = [
            ThrottleLimit(
                scope=f"operational_{action}_user",
                key=str(request.user.pk),
                limit=limit,
                window_seconds=60 * 60,
            ),
            ThrottleLimit(
                scope=f"operational_{action}_ip",
                key=request_ip(request),
                limit=limit * 3,
                window_seconds=60 * 60,
            ),
    ]
    if target_email:
        limits.append(
            ThrottleLimit(
                scope=f"operational_{action}_email",
                key=target_email,
                limit=limit,
                window_seconds=60 * 60,
            )
        )
    return reserve_throttle_attempts(limits=tuple(limits))


def _platform_feed():
    items = []
    for event in PlatformActivityEvent.objects.select_related("actor_user")[:8]:
        items.append(
            {
                "when": event.created_at,
                "label": "Plataforma",
                "title": event.get_event_type_display(),
                "detail": event.summary,
                "tone": "neutral",
                "url": reverse("notifications:superadmin_notifications"),
                "action_label": "Abrir avisos",
            }
        )
    for execution in BackupExecution.objects.order_by("-started_at")[:6]:
        items.append(
            {
                "when": execution.started_at,
                "label": "Copia de seguridad",
                "title": execution.get_status_display(),
                "detail": execution.get_destination_display(),
                "tone": "danger" if execution.status == "failed" else "success",
                "url": reverse("dashboards:superadmin_continuity"),
                "action_label": "Ver continuidad",
            }
        )
    for receipt in DemoRefreshReceipt.objects.order_by("-completed_at")[:6]:
        items.append(
            {
                "when": receipt.completed_at,
                "label": "Demostración",
                "title": "Regeneración completada",
                "detail": f"Datos reconstruidos con fecha base {receipt.base_date:%d/%m/%Y}.",
                "tone": "success",
                "url": reverse("dashboards:superadmin_continuity"),
                "action_label": "Ver continuidad",
            }
        )
    for signup in BusinessSignupRequest.objects.filter(
        status__in=BusinessSignupRequest.open_statuses()
    ).order_by("-created_at")[:6]:
        items.append(
            {
                "when": signup.created_at,
                "label": "Solicitud de alta",
                "title": signup.business_name,
                "detail": signup.get_status_display(),
                "tone": "neutral",
                "url": reverse(
                    "businesses:superadmin_signup_request_detail",
                    args=[signup.pk],
                ),
                "action_label": "Revisar solicitud",
            }
        )
    notice_labels = {
        "verification": "Verificación del canal",
        "test": "Prueba del canal",
        "signup_request": "Solicitud de alta",
        "business_created": "Negocio creado",
        "professional_activated": "Profesional activado",
        "continuity_succeeded": "Continuidad recuperada",
        "continuity_failed": "Incidencia de continuidad",
        "demo_refresh_requested": "Regeneración solicitada",
        "demo_refresh_completed": "Regeneración completada",
        "demo_refresh_failed": "Regeneración fallida",
        "email_failure": "Fallo definitivo de correo",
        "holiday_impact": "Impacto de festivos",
        "holiday_review": "Revisión por festivo",
    }
    for email in OutboundEmail.objects.filter(
        kind=OutboundEmail.Kind.OPERATIONAL_NOTICE
    ).order_by("-updated_at")[:8]:
        items.append(
            {
                "when": email.updated_at,
                "label": "Correo operativo",
                "title": notice_labels.get(email.payload.get("code"), "Aviso operativo"),
                "detail": email.operational_status_label,
                "tone": (
                    "danger"
                    if email.status == OutboundEmail.Status.FAILED
                    else "success"
                    if email.status == OutboundEmail.Status.SENT
                    else "neutral"
                ),
                "url": reverse("notifications:superadmin_notifications"),
                "action_label": "Revisar correo",
            }
        )
    return sorted(items, key=lambda item: item["when"], reverse=True)[:12]


def _platform_context(form=None):
    platform_settings = PlatformSettings.objects.filter(
        pk=PlatformSettings.SINGLETON_PK
    ).first() or PlatformSettings(pk=PlatformSettings.SINGLETON_PK)
    return {
        "form": form
        or PlatformNotificationSettingsForm(instance=platform_settings),
        "platform_settings": platform_settings,
        "feed": _platform_feed(),
        "failed_email_count": OutboundEmail.objects.filter(
            status=OutboundEmail.Status.FAILED
        ).count(),
    }


@login_required
@require_GET
def superadmin_notifications(request):
    _require_feature()
    _require_superadmin(request)
    return render(request, "superadmin/notifications.html", _platform_context())


@login_required
@require_POST
def platform_notification_settings(request):
    _require_feature()
    _require_superadmin(request)
    platform_settings, _created = PlatformSettings.objects.get_or_create(
        pk=PlatformSettings.SINGLETON_PK
    )
    form = PlatformNotificationSettingsForm(
        request.POST,
        instance=platform_settings,
        actor=request.user,
    )
    if not form.is_valid():
        return render(
            request,
            "superadmin/notifications.html",
            _platform_context(form),
            status=400,
        )

    intent = request.POST.get("intent", "save")
    settings_changed = bool(form.changed_data)
    if settings_changed:
        settings_reservation = _reserve_email_action(
            request,
            action="settings",
            limit=20,
            target_email=form.cleaned_data["notification_email"],
        )
        if not settings_reservation.allowed:
            messages.error(request, "Espera antes de volver a cambiar los avisos.")
            return redirect("notifications:superadmin_notifications")
        with transaction.atomic():
            platform_settings = form.save()
            PlatformActivityEvent.objects.create(
                actor_user=request.user,
                event_type=PlatformActivityEvent.EventType.NOTIFICATION_SETTINGS_UPDATED,
                summary="Se actualizaron el canal y las preferencias de avisos.",
                changes={
                    "channel_configured": bool(
                        platform_settings.notification_email_normalized
                    ),
                    "channel_verified": bool(
                        platform_settings.notification_email_verified_at
                    ),
                    "enabled": platform_settings.notifications_enabled,
                    "preferences": {
                        name: bool(getattr(platform_settings, name))
                        for name in (
                            "notify_continuity",
                            "notify_demo_refresh",
                            "notify_signup_requests",
                            "notify_email_failures",
                        )
                    },
                },
            )

    verification_pending = bool(
        platform_settings.notification_email_normalized
        and platform_settings.notification_email_verified_at is None
    )
    should_send_verification = verification_pending and (
        "notification_email" in form.changed_data or intent == "resend"
    )
    if should_send_verification:
        reservation = _reserve_email_action(
            request,
            action="verification",
            target_email=platform_settings.notification_email_normalized,
        )
        if reservation.allowed:
            verification_delivery = queue_operational_email_verification_safely(
                scope="platform",
                target=platform_settings,
            )
            if (
                verification_delivery is not None
                and verification_delivery.status == OutboundEmail.Status.SENT
            ):
                messages.info(
                    request,
                    "Correo guardado. El servicio de correo ha aceptado el enlace de "
                    "verificación; revisa esa bandeja para confirmar la dirección.",
                )
            elif verification_delivery is not None and verification_delivery.status in {
                OutboundEmail.Status.PENDING,
                OutboundEmail.Status.PROCESSING,
            }:
                messages.info(
                    request,
                    "Correo guardado. El enlace de verificación está en cola y se "
                    "volverá a intentar automáticamente.",
                )
            elif not operational_notification_delivery_enabled():
                messages.warning(
                    request,
                    "Correo guardado. La entrega externa está pausada; podrás reenviar "
                    "la verificación cuando vuelva a estar disponible.",
                )
            else:
                messages.warning(
                    request,
                    "Correo guardado, pero el enlace no ha podido prepararse. Inténtalo de nuevo.",
                )
        else:
            messages.warning(
                request,
                "Correo guardado. Espera antes de solicitar otro enlace de verificación.",
            )
    elif not settings_changed:
        messages.info(request, "No había cambios que guardar.")
    elif verification_pending:
        messages.info(
            request,
            "Los cambios quedan guardados. El correo continúa pendiente de verificar.",
        )
    else:
        messages.success(request, "Los avisos de AgendaSalon quedan guardados.")
    return redirect("notifications:superadmin_notifications")


def _business_for_request(request):
    if not request.user.is_active:
        raise PermissionDenied
    business = get_primary_business_for_user(request.user)
    if business is None or request.user.is_superuser:
        raise PermissionDenied
    return business


def _queue_test_for_scope(request, *, scope):
    if scope == "platform":
        _require_superadmin(request)
        target = PlatformSettings.objects.get(pk=PlatformSettings.SINGLETON_PK)
        business = None
        destination = "notifications:superadmin_notifications"
    else:
        target = _business_for_request(request)
        business = target
        destination = "business_settings:professional_settings"

    submitted_email = (request.POST.get("notification_email") or "").strip().lower()
    if submitted_email and submitted_email != target.notification_email_normalized:
        messages.warning(request, "Guarda primero el nuevo correo antes de enviar la prueba.")
        return redirect(destination)

    reservation = _reserve_email_action(
        request,
        action="test",
        target_email=target.notification_email_normalized,
    )
    if not reservation.allowed:
        messages.error(request, "Espera antes de enviar otra prueba.")
        return redirect(destination)
    email = queue_operational_test(
        scope=scope,
        target=target,
        business=business,
        action_path=reverse(destination),
    )
    if email is None:
        messages.error(request, "Verifica y activa el correo antes de enviar una prueba.")
    else:
        delivery_summary = "Correo de prueba en cola."
        if scope == "platform":
            PlatformActivityEvent.objects.create(
                actor_user=request.user,
                event_type=PlatformActivityEvent.EventType.NOTIFICATION_TEST_QUEUED,
                summary=delivery_summary,
            )
        else:
            record_business_activity(
                business=business,
                category=BusinessActivityEvent.Category.CONFIGURATION,
                event_type=BusinessActivityEvent.EventType.NOTIFICATION_SETTINGS_UPDATED,
                origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                summary=delivery_summary,
                actor=request.user,
                entity=business,
                entity_type="business",
                changes={"test_queued": True},
            )
        messages.info(request, "Correo de prueba en cola.")
    return redirect(destination)


@login_required
@require_POST
def platform_email_test(request):
    _require_feature()
    return _queue_test_for_scope(request, scope="platform")


@login_required
@require_POST
def business_email_test(request):
    _require_feature()
    return _queue_test_for_scope(request, scope="business")


def _verification_response(request, *, scope, token):
    if scope == "platform":
        _require_superadmin(request)
        destination = "notifications:superadmin_notifications"
        business = None
    else:
        business = _business_for_request(request)
        destination = "business_settings:professional_settings"

    target = operational_email_target_from_token(token, scope=scope)
    if target is None:
        response = render(
            request,
            "notifications/operational_email_invalid.html",
            {"scope": scope, "destination": destination},
            status=410,
        )
    else:
        if business is not None and business.pk != target.pk:
            raise PermissionDenied

        if request.method == "POST":
            reservation = _reserve_email_action(
                request,
                action="verify",
                limit=8,
                target_email=target.notification_email_normalized,
            )
            if not reservation.allowed:
                response = render(
                    request,
                    "notifications/operational_email_invalid.html",
                    {
                        "rate_limited": True,
                        "scope": scope,
                        "destination": destination,
                    },
                    status=429,
                )
            else:
                verified = verify_operational_email(token, scope=scope)
                if verified is None:
                    response = render(
                        request,
                        "notifications/operational_email_invalid.html",
                        {"scope": scope, "destination": destination},
                        status=410,
                    )
                else:
                    settle_successful_throttle(reservation)
                    if scope == "platform":
                        PlatformActivityEvent.objects.create(
                            actor_user=request.user,
                            event_type=(
                                PlatformActivityEvent.EventType.NOTIFICATION_EMAIL_VERIFIED
                            ),
                            summary="Se verificó el correo de avisos de la plataforma.",
                        )
                    else:
                        record_business_activity(
                            business=target,
                            category=BusinessActivityEvent.Category.CONFIGURATION,
                            event_type=(
                                BusinessActivityEvent.EventType.NOTIFICATION_SETTINGS_UPDATED
                            ),
                            origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                            summary="Se verificó el correo de avisos del negocio.",
                            actor=request.user,
                            entity=target,
                            entity_type="business",
                            changes={"channel_verified": True},
                        )
                    messages.success(request, "El correo de avisos queda verificado.")
                    response = redirect(destination)
        else:
            response = render(
                request,
                "notifications/operational_email_verify.html",
                {"target": target, "scope": scope},
            )
    response["Referrer-Policy"] = (
        "strict-origin"
        if target is not None and request.method in {"GET", "HEAD"}
        else "no-referrer"
    )
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def platform_email_verify(request, token):
    _require_feature()
    return _verification_response(request, scope="platform", token=token)


@login_required
@require_http_methods(["GET", "HEAD", "POST"])
def business_email_verify(request, token):
    _require_feature()
    return _verification_response(request, scope="business", token=token)

from functools import wraps

import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.booking.models import Appointment, BusinessCalendarSettings
from apps.businesses.activity import record_business_activity
from apps.businesses.forms import (
    BusinessForm,
    BusinessSignupRequestReviewForm,
    BusinessVisualSettingsForm,
    PlatformVisualSettingsForm,
    ProfessionalCreateForm,
)
from apps.businesses.models import (
    Business,
    BusinessActivityEvent,
    BusinessMembership,
    BusinessSignupRequest,
    PlatformSettings,
)
from apps.businesses.services import (
    get_business_public_image_url,
    get_business_visual_theme,
    get_platform_login_image_url,
    get_primary_business_for_user,
)
from apps.holidays.forms import NationalHolidaySyncForm
from apps.holidays.appointment_reviews import pending_holiday_business_summaries
from apps.holidays.models import OfficialHoliday
from apps.holidays.services import (
    BOE_NETWORK_ERROR,
    BoeSyncError,
    latest_boe_national_holiday_run,
    sync_boe_national_holidays,
)
from apps.core.security_throttle import (
    ThrottleLimit,
    request_ip,
    reserve_throttle_attempts,
)
from apps.core.features import (
    operational_notification_delivery_enabled,
    operational_notifications_enabled,
    transactional_email_delivery_enabled,
)
from apps.legal.models import LegalAcceptance, LegalAcceptanceEvent
from apps.notifications.forms import BusinessNotificationSettingsForm
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    queue_and_dispatch,
    queue_operational_email_verification_safely,
    queue_operational_notice_on_commit,
    queue_professional_activation,
)
from apps.legal.services import business_legal_status


ACTIVITY_FILTERS = (
    ("all", "Todo"),
    (BusinessActivityEvent.Category.APPOINTMENTS, "Citas"),
    (BusinessActivityEvent.Category.CONFIGURATION, "Configuración"),
    (BusinessActivityEvent.Category.ACCESS, "Accesos"),
    (BusinessActivityEvent.Category.PLATFORM, "Plataforma"),
)


def superadmin_required(view_func):
    @wraps(view_func)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapped


def _business_queryset():
    return Business.objects.annotate(
        professionals_total=Count(
            "memberships",
            filter=Q(memberships__is_active=True),
            distinct=True,
        ),
        services_total=Count("services", filter=Q(services__is_active=True), distinct=True),
        clients_total=Count("clients", filter=Q(clients__is_active=True), distinct=True),
        appointments_total=Count("appointments", distinct=True),
    )


@superadmin_required
def superadmin_business_list(request):
    businesses = _business_queryset().order_by("commercial_name", "pk")
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "all")
    if query:
        businesses = businesses.filter(
            Q(commercial_name__icontains=query)
            | Q(slug__icontains=query)
            | Q(city__icontains=query)
            | Q(public_phone__icontains=query)
        )
    if status == "active":
        businesses = businesses.filter(is_active=True)
    elif status == "inactive":
        businesses = businesses.filter(is_active=False)
    elif status == "public":
        businesses = businesses.filter(is_active=True, public_booking_enabled=True)
    elif status == "private":
        businesses = businesses.filter(public_booking_enabled=False)

    return render(
        request,
        "superadmin/businesses/list.html",
        {
            "businesses": businesses,
            "query": query,
            "status": status,
            "result_count": businesses.count(),
            "open_signup_requests_count": BusinessSignupRequest.objects.filter(
                status__in=BusinessSignupRequest.open_statuses()
            ).count(),
        },
    )


@superadmin_required
def superadmin_business_create(request):
    signup_request_id = request.POST.get("signup_request_id") or request.GET.get("solicitud")
    signup_request = None
    if signup_request_id:
        signup_request = get_object_or_404(BusinessSignupRequest, pk=signup_request_id)
        if signup_request.status not in BusinessSignupRequest.open_statuses():
            messages.error(request, "Esta solicitud ya no se puede convertir en un negocio.")
            return redirect(
                "businesses:superadmin_signup_request_detail",
                request_id=signup_request.pk,
            )

    business_initial = {}
    professional_initial = {}
    if signup_request is not None:
        business_initial = {
            "commercial_name": signup_request.business_name,
            "city": signup_request.city,
            "province": signup_request.province,
        }
        professional_initial = {
            "full_name": signup_request.contact_name,
            "phone": signup_request.phone,
            "email": signup_request.email,
        }

    business_form = BusinessForm(request.POST or None, initial=business_initial)
    professional_form = ProfessionalCreateForm(request.POST or None, initial=professional_initial)
    if request.method == "POST":
        business_valid = business_form.is_valid()
        professional_valid = professional_form.is_valid()
        if business_valid and professional_valid:
            with transaction.atomic():
                locked_signup_request = None
                if signup_request is not None:
                    locked_signup_request = BusinessSignupRequest.objects.select_for_update().get(
                        pk=signup_request.pk
                    )
                    if locked_signup_request.status in {
                        BusinessSignupRequest.Status.CONVERTED,
                        BusinessSignupRequest.Status.DISMISSED,
                    }:
                        messages.error(
                            request,
                            "Esta solicitud ya no se puede convertir en un negocio.",
                        )
                        return redirect(
                            "businesses:superadmin_signup_request_detail",
                            request_id=locked_signup_request.pk,
                        )
                business = business_form.save()
                business.legal_compliance_enabled = True
                business.save(update_fields=["legal_compliance_enabled", "updated_at"])
                BusinessCalendarSettings.objects.create(business=business)
                professional = professional_form.create_professional(business=business)
                business.notification_email = professional.email
                business.notification_email_normalized = professional.email_normalized
                business.notification_email_verified_at = professional.email_verified_at
                business.save(
                    update_fields=[
                        "notification_email",
                        "notification_email_normalized",
                        "notification_email_verified_at",
                        "updated_at",
                    ]
                )
                record_business_activity(
                    business=business,
                    category=BusinessActivityEvent.Category.PLATFORM,
                    event_type=BusinessActivityEvent.EventType.BUSINESS_CREATED,
                    origin=BusinessActivityEvent.Origin.PLATFORM,
                    summary="Negocio dado de alta en AgendaSalon.",
                    actor=request.user,
                    entity=business,
                    entity_type="business",
                )
                record_business_activity(
                    business=business,
                    category=BusinessActivityEvent.Category.ACCESS,
                    event_type=BusinessActivityEvent.EventType.MEMBERSHIP_CREATED,
                    origin=BusinessActivityEvent.Origin.PLATFORM,
                    summary=f"Acceso profesional creado para {professional.full_name}.",
                    actor=request.user,
                    entity_type="business_membership",
                )
                if locked_signup_request is not None:
                    locked_signup_request.status = BusinessSignupRequest.Status.CONVERTED
                    locked_signup_request.converted_business = business
                    locked_signup_request.converted_at = timezone.now()
                    locked_signup_request.handled_by = request.user
                    locked_signup_request.save(
                        update_fields=[
                            "status",
                            "converted_business",
                            "converted_at",
                            "handled_by",
                            "updated_at",
                        ]
                    )
            delivery = queue_and_dispatch(
                queue_professional_activation(professional, business=business)
            )
            queue_operational_notice_on_commit(
                scope="platform",
                code="business_created",
                deduplication_key=f"business-created:{business.pk}",
                action_path=reverse(
                    "businesses:superadmin_business_detail",
                    args=[business.pk],
                ),
            )
            if not transactional_email_delivery_enabled():
                messages.info(
                    request,
                    f"{business.commercial_name} ya está creado y el acceso de "
                    f"{professional.full_name} queda preparado. El correo externo de "
                    "activación está desactivado en esta demostración académica.",
                )
            elif delivery.status == delivery.Status.SENT:
                messages.success(
                    request,
                    f"{business.commercial_name} queda dado de alta. El servicio de correo ha aceptado el enlace de activación para {professional.email}.",
                )
            else:
                messages.warning(
                    request,
                    f"{business.commercial_name} queda dado de alta, pero el correo de activación está pendiente de envío.",
                )
            return redirect("businesses:superadmin_business_detail", business_id=business.id)

    return render(
        request,
        "superadmin/businesses/form.html",
        {
            "business_form": business_form,
            "professional_form": professional_form,
            "editing": False,
            "signup_request": signup_request,
        },
    )


@superadmin_required
def superadmin_signup_request_list(request):
    signup_requests = BusinessSignupRequest.objects.select_related(
        "handled_by", "converted_business"
    )
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "all")
    if query:
        signup_requests = signup_requests.filter(
            Q(business_name__icontains=query)
            | Q(contact_name__icontains=query)
            | Q(city__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
        )
    valid_statuses = {choice.value for choice in BusinessSignupRequest.Status}
    if status in valid_statuses:
        signup_requests = signup_requests.filter(status=status)
    else:
        status = "all"

    result_count = signup_requests.count()
    page = Paginator(signup_requests, 20).get_page(request.GET.get("page"))
    return render(
        request,
        "superadmin/businesses/signup_requests/list.html",
        {
            "signup_requests": page,
            "page": page,
            "query": query,
            "status": status,
            "status_choices": BusinessSignupRequest.Status.choices,
            "result_count": result_count,
            "new_count": BusinessSignupRequest.objects.filter(
                status=BusinessSignupRequest.Status.NEW
            ).count(),
        },
    )


@superadmin_required
def superadmin_signup_request_detail(request, request_id):
    signup_request = get_object_or_404(
        BusinessSignupRequest.objects.select_related(
            "privacy_document", "handled_by", "converted_business"
        ),
        pk=request_id,
    )
    is_converted = signup_request.status == BusinessSignupRequest.Status.CONVERTED
    form = None
    if not is_converted:
        form = BusinessSignupRequestReviewForm(request.POST or None, instance=signup_request)
        if request.method == "POST" and form.is_valid():
            reviewed_request = form.save(commit=False)
            reviewed_request.handled_by = request.user
            reviewed_request.save()
            messages.success(request, "El seguimiento de la solicitud se ha actualizado.")
            return redirect(
                "businesses:superadmin_signup_request_detail",
                request_id=signup_request.pk,
            )

    return render(
        request,
        "superadmin/businesses/signup_requests/detail.html",
        {
            "signup_request": signup_request,
            "review_form": form,
            "is_converted": is_converted,
        },
    )


@superadmin_required
def superadmin_business_detail(request, business_id):
    business = get_object_or_404(_business_queryset(), pk=business_id)
    memberships = business.memberships.select_related("user").order_by("-is_active", "user__full_name")
    now = timezone.now()
    pending_closure_count = business.appointments.filter(
        status=Appointment.Status.CONFIRMED,
        ends_at__lte=now,
    ).count()
    upcoming_count = business.appointments.filter(
        status=Appointment.Status.CONFIRMED,
        ends_at__gt=now,
    ).count()
    activity_category = _activity_category(request.GET.get("activity", "all"))
    activity_queryset = _business_activity_queryset(business, activity_category)
    channel_counts = {
        item["manual_channel"]: item["total"]
        for item in business.appointments.values("manual_channel").annotate(total=Count("id"))
    }
    online_appointments_count = channel_counts.get(Appointment.ManualChannel.PUBLIC_WEB, 0)
    professional_appointments_count = sum(
        total
        for channel, total in channel_counts.items()
        if channel != Appointment.ManualChannel.PUBLIC_WEB
    )
    legal_status = business_legal_status(business)
    return render(
        request,
        "superadmin/businesses/detail.html",
        {
            "business": business,
            "memberships": memberships,
            "pending_closure_count": pending_closure_count,
            "upcoming_count": upcoming_count,
            "activity_events": tuple(activity_queryset[:6]),
            "activity_total": activity_queryset.count(),
            "activity_category": activity_category,
            "activity_filters": ACTIVITY_FILTERS,
            "online_appointments_count": online_appointments_count,
            "professional_appointments_count": professional_appointments_count,
            "legal_status": legal_status,
            "transactional_email_enabled": transactional_email_delivery_enabled(),
        },
    )


@superadmin_required
def superadmin_business_legal_evidence(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    legal_status = business_legal_status(business)
    acceptance_history = (
        LegalAcceptanceEvent.objects.filter(
            business=business,
            actor_user__isnull=False,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
        )
        .select_related("document", "actor_user")
        .order_by("-accepted_at", "-pk")
    )
    return render(
        request,
        "superadmin/businesses/legal_evidence.html",
        {
            "business": business,
            "legal_status": legal_status,
            "acceptance_history": acceptance_history,
        },
    )


@superadmin_required
def superadmin_business_activity(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    activity_category = _activity_category(request.GET.get("activity", "all"))
    activity_queryset = _business_activity_queryset(business, activity_category)

    activity_page = Paginator(activity_queryset, 10).get_page(request.GET.get("page"))

    return render(
        request,
        "superadmin/businesses/activity.html",
        {
            "business": business,
            "activity_events": activity_page,
            "activity_page": activity_page,
            "activity_category": activity_category,
            "activity_filters": ACTIVITY_FILTERS,
        },
    )


@superadmin_required
def superadmin_business_edit(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    business_form = BusinessForm(request.POST or None, instance=business)
    if request.method == "POST" and business_form.is_valid():
        changed_fields = tuple(business_form.changed_data)
        changed_labels = tuple(
            business_form.fields[field_name].label
            for field_name in changed_fields
            if field_name in business_form.fields
        )
        with transaction.atomic():
            business = business_form.save()
            if changed_labels:
                record_business_activity(
                    business=business,
                    category=BusinessActivityEvent.Category.PLATFORM,
                    event_type=BusinessActivityEvent.EventType.BUSINESS_UPDATED,
                    origin=BusinessActivityEvent.Origin.PLATFORM,
                    summary=f"Datos del negocio actualizados: {', '.join(changed_labels)}.",
                    actor=request.user,
                    entity=business,
                    entity_type="business",
                    changes={"fields": changed_fields},
                )
        messages.success(request, f"Los datos de {business.commercial_name} quedan actualizados.")
        return redirect("businesses:superadmin_business_detail", business_id=business.id)
    return render(
        request,
        "superadmin/businesses/form.html",
        {
            "business_form": business_form,
            "professional_form": None,
            "business": business,
            "editing": True,
        },
    )


@superadmin_required
@require_POST
def superadmin_business_toggle(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    business.is_active = not business.is_active
    business.save(update_fields=["is_active", "updated_at"])
    action = "reactivado" if business.is_active else "pausado"
    record_business_activity(
        business=business,
        category=BusinessActivityEvent.Category.PLATFORM,
        event_type=(
            BusinessActivityEvent.EventType.BUSINESS_REACTIVATED
            if business.is_active
            else BusinessActivityEvent.EventType.BUSINESS_PAUSED
        ),
        origin=BusinessActivityEvent.Origin.PLATFORM,
        summary=f"Negocio {action} sin eliminar su historial.",
        actor=request.user,
        entity=business,
        entity_type="business",
        changes={"is_active": business.is_active},
    )
    messages.success(request, f"{business.commercial_name} queda {action} sin perder su historial.")
    return redirect("businesses:superadmin_business_detail", business_id=business.id)


@superadmin_required
@require_POST
def superadmin_public_booking_toggle(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    if not business.is_active and not business.public_booking_enabled:
        messages.error(request, "Reactiva primero el negocio para abrir la reserva pública.")
    else:
        business.public_booking_enabled = not business.public_booking_enabled
        business.save(update_fields=["public_booking_enabled", "updated_at"])
        state = "activa" if business.public_booking_enabled else "pausada"
        record_business_activity(
            business=business,
            category=BusinessActivityEvent.Category.PLATFORM,
            event_type=(
                BusinessActivityEvent.EventType.PUBLIC_BOOKING_ENABLED
                if business.public_booking_enabled
                else BusinessActivityEvent.EventType.PUBLIC_BOOKING_DISABLED
            ),
            origin=BusinessActivityEvent.Origin.PLATFORM,
            summary=f"Reserva pública {state}.",
            actor=request.user,
            entity=business,
            entity_type="business",
            changes={"public_booking_enabled": business.public_booking_enabled},
        )
        messages.success(request, f"La reserva pública de {business.commercial_name} queda {state}.")
    return redirect("businesses:superadmin_business_detail", business_id=business.id)


@superadmin_required
def superadmin_professional_create(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    professional_form = ProfessionalCreateForm(request.POST or None)
    if request.method == "POST" and professional_form.is_valid():
        with transaction.atomic():
            professional = professional_form.create_professional(business=business)
            membership = BusinessMembership.objects.get(business=business, user=professional)
            if (
                not business.notification_email_normalized
                and BusinessMembership.objects.filter(business=business).count() == 1
            ):
                business.notification_email = professional.email
                business.notification_email_normalized = professional.email_normalized
                business.notification_email_verified_at = professional.email_verified_at
                business.save(
                    update_fields=[
                        "notification_email",
                        "notification_email_normalized",
                        "notification_email_verified_at",
                        "updated_at",
                    ]
                )
            record_business_activity(
                business=business,
                category=BusinessActivityEvent.Category.ACCESS,
                event_type=BusinessActivityEvent.EventType.MEMBERSHIP_CREATED,
                origin=BusinessActivityEvent.Origin.PLATFORM,
                summary=f"Acceso profesional creado para {professional.full_name}.",
                actor=request.user,
                entity=membership,
                entity_type="business_membership",
            )
        delivery = queue_and_dispatch(
            queue_professional_activation(professional, business=business)
        )
        if not transactional_email_delivery_enabled():
            messages.info(
                request,
                f"El acceso de {professional.full_name} queda preparado. El correo "
                "externo de activación está desactivado en esta demostración académica.",
            )
        elif delivery.status == delivery.Status.SENT:
            messages.success(
                request,
                f"Acceso preparado. El servicio de correo ha aceptado el enlace de activación para {professional.email}.",
            )
        else:
            messages.warning(
                request,
                f"El acceso de {professional.full_name} está preparado, pero el correo sigue pendiente de envío.",
            )
        return redirect("businesses:superadmin_business_detail", business_id=business.id)
    return render(
        request,
        "superadmin/businesses/professional_form.html",
        {"business": business, "professional_form": professional_form},
    )


@superadmin_required
@require_POST
def superadmin_professional_activation_resend(request, business_id, membership_id):
    membership = get_object_or_404(
        BusinessMembership.objects.select_related("user", "business"),
        pk=membership_id,
        business_id=business_id,
    )
    user = membership.user
    if user.is_active or not user.email_normalized:
        messages.error(request, "Esta cuenta no necesita un enlace de activación.")
    else:
        delivery = queue_and_dispatch(
            queue_professional_activation(user, business=membership.business)
        )
        if not transactional_email_delivery_enabled():
            messages.info(
                request,
                f"La cuenta de {user.full_name} sigue preparada. El correo externo de "
                "activación está desactivado en esta demostración académica.",
            )
        elif delivery.status == delivery.Status.SENT:
            messages.success(request, f"Enlace de activación reenviado a {user.email}.")
        else:
            messages.warning(request, "El reenvío ha quedado pendiente. No se ha perdido la solicitud.")
    return redirect("businesses:superadmin_business_detail", business_id=business_id)


@superadmin_required
@require_POST
def superadmin_membership_toggle(request, business_id, membership_id):
    membership = get_object_or_404(
        BusinessMembership.objects.select_related("user", "business"),
        pk=membership_id,
        business_id=business_id,
    )
    membership.is_active = not membership.is_active
    membership.save(update_fields=["is_active", "updated_at"])
    state = "reactivado" if membership.is_active else "pausado"
    record_business_activity(
        business=membership.business,
        category=BusinessActivityEvent.Category.ACCESS,
        event_type=(
            BusinessActivityEvent.EventType.MEMBERSHIP_REACTIVATED
            if membership.is_active
            else BusinessActivityEvent.EventType.MEMBERSHIP_PAUSED
        ),
        origin=BusinessActivityEvent.Origin.PLATFORM,
        summary=f"Acceso de {membership.user.full_name} {state}.",
        actor=request.user,
        entity=membership,
        entity_type="business_membership",
        changes={"is_active": membership.is_active},
    )
    messages.success(request, f"El acceso de {membership.user.full_name} queda {state}.")
    return redirect("businesses:superadmin_business_detail", business_id=business_id)


@superadmin_required
def superadmin_platform_settings(request):
    platform_settings, _created = PlatformSettings.objects.get_or_create(
        pk=PlatformSettings.SINGLETON_PK
    )
    settings_form = PlatformVisualSettingsForm(
        request.POST or None,
        request.FILES or None,
        instance=platform_settings,
    )
    if request.method == "POST" and settings_form.is_valid():
        theme_changed = "admin_theme" in settings_form.changed_data
        image_uploaded = "new_login_image" in settings_form.changed_data
        image_selected = "login_image_choice" in settings_form.changed_data
        appearance_changed = theme_changed or image_uploaded or image_selected

        with transaction.atomic():
            platform_settings = settings_form.save(updated_by=request.user)

        if appearance_changed:
            messages.success(request, "Los ajustes de AgendaSalon quedan guardados.")
        else:
            messages.info(request, "No había cambios pendientes en la apariencia.")
        return redirect("platform_settings:superadmin_platform_settings")

    latest_holiday_run = latest_boe_national_holiday_run()
    try:
        holiday_year = int(request.GET.get("holiday_year", ""))
    except (TypeError, ValueError):
        holiday_year = latest_holiday_run.year if latest_holiday_run else timezone.localdate().year
    if not 2000 <= holiday_year <= 2100:
        holiday_year = timezone.localdate().year

    national_holidays = OfficialHoliday.objects.filter(
        year=holiday_year,
        scope=OfficialHoliday.Scope.NATIONAL,
    ).order_by("date", "name")
    holiday_years = tuple(
        OfficialHoliday.objects.filter(scope=OfficialHoliday.Scope.NATIONAL)
        .order_by()
        .values_list("year", flat=True)
        .distinct()
    )
    holiday_business_impacts = pending_holiday_business_summaries()

    return render(
        request,
        "superadmin/settings.html",
        {
            "platform_settings": platform_settings,
            "settings_form": settings_form,
            "login_image_url": get_platform_login_image_url(platform_settings),
            "login_image_is_custom": platform_settings.login_images.filter(
                is_selected=True
            ).exists(),
            "holiday_sync_form": NationalHolidaySyncForm(initial={"year": holiday_year}),
            "holiday_year": holiday_year,
            "holiday_years": holiday_years,
            "national_holidays": national_holidays,
            "latest_holiday_run": latest_boe_national_holiday_run(year=holiday_year),
            "holiday_business_impacts": holiday_business_impacts,
            "holiday_business_impact_total": sum(
                item.appointment_count for item in holiday_business_impacts
            ),
        },
    )


@superadmin_required
@require_POST
def superadmin_holiday_sync(request):
    form = NationalHolidaySyncForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indica un año válido entre 2000 y 2100.")
        return redirect("platform_settings:superadmin_platform_settings")

    target_year = form.cleaned_data["year"]
    try:
        result = sync_boe_national_holidays(target_year, created_by=request.user)
    except requests.RequestException:
        messages.error(
            request,
            f"No se pudo sincronizar el calendario BOE de {target_year}: "
            f"{BOE_NETWORK_ERROR}",
        )
    except BoeSyncError as error:
        messages.error(
            request,
            f"No se pudo sincronizar el calendario BOE de {target_year}: {error}",
        )
    else:
        run = result.run
        created_label = "creado" if run.items_created == 1 else "creados"
        updated_label = "actualizado" if run.items_updated == 1 else "actualizados"
        removed_label = "retirado" if run.items_removed == 1 else "retirados"
        messages.success(
            request,
            (
                f"Calendario BOE {target_year} sincronizado: "
                f"{run.items_created} {created_label}, "
                f"{run.items_updated} {updated_label} y "
                f"{run.items_removed} {removed_label}."
            ),
        )
        if run.affected_appointments:
            run_reference = getattr(run, "pk", None) or (
                f"{target_year}:{run.items_created}:{run.items_updated}:"
                f"{run.items_removed}:{run.affected_appointments}:"
                f"{run.affected_businesses}"
            )
            queue_operational_notice_on_commit(
                scope="platform",
                code="holiday_impact",
                deduplication_key=f"holiday-impact:{run_reference}",
                action_path=(
                    f"{reverse('platform_settings:superadmin_platform_settings')}"
                    f"?holiday_year={target_year}#festivos-nacionales"
                ),
                context={
                    "appointments": run.affected_appointments,
                    "businesses": run.affected_businesses,
                },
            )
            for impact in pending_holiday_business_summaries(year=target_year):
                impacted_business = Business.objects.filter(pk=impact.business_id).first()
                if impacted_business is None:
                    continue
                queue_operational_notice_on_commit(
                    scope="business",
                    code="holiday_review",
                    deduplication_key=(
                        f"holiday-impact:{run_reference}:{impact.business_id}"
                    ),
                    business=impacted_business,
                    action_path=reverse("booking:professional_schedule"),
                    context={"appointments": impact.appointment_count},
                )
            appointment_text = (
                "1 cita futura"
                if run.affected_appointments == 1
                else f"{run.affected_appointments} citas futuras"
            )
            business_text = (
                "1 negocio"
                if run.affected_businesses == 1
                else f"{run.affected_businesses} negocios"
            )
            detected = "se detectó" if run.affected_appointments == 1 else "se detectaron"
            messages.warning(
                request,
                (
                    f"Al terminar {detected} {appointment_text} en "
                    f"{business_text}. No se ha cancelado ni movido ninguna."
                ),
            )
    return redirect(
        f"{reverse('platform_settings:superadmin_platform_settings')}"
        f"?holiday_year={target_year}#festivos-nacionales"
    )


@login_required
def professional_settings(request):
    if request.user.is_superuser:
        raise PermissionDenied
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    form_kind = request.POST.get("form_kind", "appearance")
    if (
        request.method == "POST"
        and form_kind == "notifications"
        and not operational_notifications_enabled()
    ):
        raise Http404
    settings_form = BusinessVisualSettingsForm(
        request.POST if request.method == "POST" and form_kind == "appearance" else None,
        request.FILES if request.method == "POST" and form_kind == "appearance" else None,
        instance=business,
    )
    notification_form = BusinessNotificationSettingsForm(
        request.POST if request.method == "POST" and form_kind == "notifications" else None,
        instance=business,
    )
    if request.method == "POST" and form_kind == "notifications" and notification_form.is_valid():
        intent = request.POST.get("intent", "save")
        settings_changed = bool(notification_form.changed_data)
        if settings_changed:
            settings_reservation = reserve_throttle_attempts(
                limits=(
                    ThrottleLimit(
                        scope="operational_settings_user",
                        key=str(request.user.pk),
                        limit=20,
                        window_seconds=60 * 60,
                    ),
                    ThrottleLimit(
                        scope="operational_settings_ip",
                        key=request_ip(request),
                        limit=60,
                        window_seconds=60 * 60,
                    ),
                    ThrottleLimit(
                        scope="operational_settings_email",
                        key=notification_form.cleaned_data["notification_email"] or "sin-correo",
                        limit=20,
                        window_seconds=60 * 60,
                    ),
                )
            )
            if not settings_reservation.allowed:
                messages.error(request, "Espera antes de volver a cambiar los avisos.")
                return redirect("business_settings:professional_settings")
            with transaction.atomic():
                business = notification_form.save()
                record_business_activity(
                    business=business,
                    category=BusinessActivityEvent.Category.CONFIGURATION,
                    event_type=BusinessActivityEvent.EventType.NOTIFICATION_SETTINGS_UPDATED,
                    origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                    summary="Se actualizaron el canal y las preferencias de avisos.",
                    actor=request.user,
                    entity=business,
                    entity_type="business",
                    changes={
                        "channel_configured": bool(business.notification_email_normalized),
                        "channel_verified": bool(business.notification_email_verified_at),
                        "enabled": business.notifications_enabled,
                        "preferences": {
                            name: bool(getattr(business, name))
                            for name in (
                                "notify_new_appointments",
                                "notify_cancellations",
                                "notify_client_access",
                                "notify_holiday_reviews",
                                "notify_email_failures",
                            )
                        },
                    },
                )
        verification_pending = bool(
            business.notification_email_normalized
            and business.notification_email_verified_at is None
        )
        should_send_verification = verification_pending and (
            "notification_email" in notification_form.changed_data or intent == "resend"
        )
        verification_reserved = None
        if should_send_verification:
            verification_reserved = reserve_throttle_attempts(
                limits=(
                    ThrottleLimit(
                        scope="operational_verification_user",
                        key=str(request.user.pk),
                        limit=5,
                        window_seconds=60 * 60,
                    ),
                    ThrottleLimit(
                        scope="operational_verification_ip",
                        key=request_ip(request),
                        limit=15,
                        window_seconds=60 * 60,
                    ),
                    ThrottleLimit(
                        scope="operational_verification_email",
                        key=business.notification_email_normalized,
                        limit=5,
                        window_seconds=60 * 60,
                    ),
                )
            )
        verification_delivery = None
        if should_send_verification and verification_reserved.allowed:
            verification_delivery = queue_operational_email_verification_safely(
                scope="business",
                target=business,
                business=business,
            )
        if (
            verification_delivery is not None
            and verification_delivery.status == OutboundEmail.Status.SENT
        ):
            messages.info(
                request,
                "Los avisos quedan guardados. El servicio de correo ha aceptado el "
                "enlace de verificación; revisa esa bandeja para confirmarlo.",
            )
        elif verification_delivery is not None and verification_delivery.status in {
            OutboundEmail.Status.PENDING,
            OutboundEmail.Status.PROCESSING,
        }:
            messages.info(
                request,
                "Los avisos quedan guardados. El enlace de verificación está en cola "
                "y se volverá a intentar automáticamente.",
            )
        elif should_send_verification and not operational_notification_delivery_enabled():
            messages.warning(
                request,
                "Los avisos quedan guardados. La entrega externa está pausada; podrás "
                "reenviar la verificación cuando vuelva a estar disponible.",
            )
        elif should_send_verification and verification_reserved.allowed:
            messages.warning(
                request,
                "Los avisos quedan guardados, pero el enlace no ha podido prepararse. "
                "Inténtalo de nuevo.",
            )
        elif should_send_verification:
            messages.warning(
                request,
                "Los avisos quedan guardados. Espera antes de solicitar otro enlace de verificación.",
            )
        elif not settings_changed:
            messages.info(request, "No había cambios que guardar.")
        elif verification_pending:
            messages.info(
                request,
                "Los cambios quedan guardados. El correo continúa pendiente de verificar.",
            )
        else:
            messages.success(request, "Los avisos del negocio quedan guardados.")
        return redirect("business_settings:professional_settings")

    if request.method == "POST" and form_kind == "appearance" and settings_form.is_valid():
        theme_changed = "professional_theme" in settings_form.changed_data
        image_uploaded = "new_public_image" in settings_form.changed_data
        image_selected = "public_image_choice" in settings_form.changed_data
        appearance_changed = theme_changed or image_uploaded or image_selected

        try:
            with transaction.atomic():
                business = settings_form.save(uploaded_by=request.user)
                if appearance_changed:
                    updated_parts = []
                    if theme_changed:
                        updated_parts.append("tema del panel")
                    if image_uploaded or image_selected:
                        updated_parts.append("imagen pública")
                    record_business_activity(
                        business=business,
                        category=BusinessActivityEvent.Category.CONFIGURATION,
                        event_type=BusinessActivityEvent.EventType.VISUAL_SETTINGS_UPDATED,
                        origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                        summary=f"Apariencia actualizada: {', '.join(updated_parts)}.",
                        actor=request.user,
                        entity=business,
                        entity_type="business",
                        changes={
                            "professional_theme": business.professional_theme,
                            "has_custom_public_image": business.public_images.filter(
                                is_selected=True
                            ).exists(),
                            "public_image_preset": business.public_image_preset,
                        },
                    )
        except ValidationError as exc:
            _delete_rolled_back_public_image(settings_form)
            if hasattr(exc, "message_dict") and "new_public_image" in exc.message_dict:
                for error in exc.message_dict["new_public_image"]:
                    settings_form.add_error("new_public_image", error)
            else:
                settings_form.add_error(None, exc)
        except Exception:
            _delete_rolled_back_public_image(settings_form)
            raise
        else:
            if appearance_changed:
                messages.success(request, "Los ajustes visuales del negocio quedan guardados.")
            else:
                messages.info(request, "No había cambios pendientes en la apariencia.")
            return redirect("business_settings:professional_settings")

    return render(
        request,
        "professional/settings.html",
        {
            "business": business,
            "settings_form": settings_form,
            "notification_form": notification_form,
            "business_failed_email_count": business.outbound_emails.filter(
                status="failed"
            ).count(),
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
            "public_image_is_custom": business.public_images.filter(is_selected=True).exists(),
        },
    )


def _delete_rolled_back_public_image(settings_form):
    saved_image = getattr(settings_form, "saved_public_image", None)
    if saved_image is None or not saved_image.image.name:
        return
    saved_image.image.storage.delete(saved_image.image.name)


def _activity_category(value):
    allowed = {filter_value for filter_value, _label in ACTIVITY_FILTERS}
    return value if value in allowed else "all"


def _business_activity_queryset(business, activity_category):
    queryset = business.activity_events.select_related("actor_user").order_by(
        "-created_at",
        "-id",
    )
    if activity_category != "all":
        queryset = queryset.filter(category=activity_category)
    return queryset

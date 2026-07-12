from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.booking.models import Appointment
from apps.businesses.activity import record_business_activity
from apps.businesses.forms import (
    BusinessForm,
    BusinessVisualSettingsForm,
    PlatformVisualSettingsForm,
    ProfessionalCreateForm,
)
from apps.businesses.models import (
    Business,
    BusinessActivityEvent,
    BusinessMembership,
    PlatformSettings,
)
from apps.businesses.services import (
    get_business_public_image_url,
    get_business_visual_theme,
    get_platform_login_image_url,
    get_primary_business_for_user,
)


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
            return HttpResponseForbidden("No tienes permiso para gestionar negocios.")
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
        },
    )


@superadmin_required
def superadmin_business_create(request):
    business_form = BusinessForm(request.POST or None)
    professional_form = ProfessionalCreateForm(request.POST or None)
    if request.method == "POST":
        business_valid = business_form.is_valid()
        professional_valid = professional_form.is_valid()
        if business_valid and professional_valid:
            with transaction.atomic():
                business = business_form.save()
                professional = professional_form.create_professional(business=business)
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
            messages.success(
                request,
                f"{business.commercial_name} queda dado de alta con su acceso profesional.",
            )
            return redirect("businesses:superadmin_business_detail", business_id=business.id)

    return render(
        request,
        "superadmin/businesses/form.html",
        {
            "business_form": business_form,
            "professional_form": professional_form,
            "editing": False,
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
    return render(
        request,
        "superadmin/businesses/detail.html",
        {
            "business": business,
            "memberships": memberships,
            "pending_closure_count": pending_closure_count,
            "upcoming_count": upcoming_count,
            "activity_events": tuple(activity_queryset[:8]),
            "activity_total": activity_queryset.count(),
            "activity_category": activity_category,
            "activity_filters": ACTIVITY_FILTERS,
            "online_appointments_count": online_appointments_count,
            "professional_appointments_count": professional_appointments_count,
        },
    )


@superadmin_required
def superadmin_business_activity(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    activity_category = _activity_category(request.GET.get("activity", "all"))
    activity_queryset = _business_activity_queryset(business, activity_category)

    before = request.GET.get("before", "").strip()
    if before.isdigit():
        activity_queryset = activity_queryset.filter(pk__lt=int(before))

    page_size = 30
    activity_events = list(activity_queryset[: page_size + 1])
    has_more_activity = len(activity_events) > page_size
    if has_more_activity:
        activity_events = activity_events[:page_size]
    next_activity_cursor = activity_events[-1].pk if has_more_activity else None

    return render(
        request,
        "superadmin/businesses/activity.html",
        {
            "business": business,
            "activity_events": activity_events,
            "activity_category": activity_category,
            "activity_filters": ACTIVITY_FILTERS,
            "has_more_activity": has_more_activity,
            "next_activity_cursor": next_activity_cursor,
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
        messages.success(request, f"{professional.full_name} ya puede entrar en {business.commercial_name}.")
        return redirect("businesses:superadmin_business_detail", business_id=business.id)
    return render(
        request,
        "superadmin/businesses/professional_form.html",
        {"business": business, "professional_form": professional_form},
    )


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
        },
    )


@login_required
def professional_settings(request):
    if request.user.is_superuser:
        return HttpResponseForbidden("Los ajustes pertenecen al negocio profesional.")
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    settings_form = BusinessVisualSettingsForm(
        request.POST or None,
        request.FILES or None,
        instance=business,
    )
    if request.method == "POST" and settings_form.is_valid():
        theme_changed = "professional_theme" in settings_form.changed_data
        image_uploaded = "new_public_image" in settings_form.changed_data
        image_selected = "public_image_choice" in settings_form.changed_data
        appearance_changed = theme_changed or image_uploaded or image_selected

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
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
            "public_image_is_custom": business.public_images.filter(is_selected=True).exists(),
        },
    )


def _activity_category(value):
    allowed = {filter_value for filter_value, _label in ACTIVITY_FILTERS}
    return value if value in allowed else "all"


def _business_activity_queryset(business, activity_category):
    queryset = business.activity_events.select_related("actor_user").order_by("-id")
    if activity_category != "all":
        queryset = queryset.filter(category=activity_category)
    return queryset

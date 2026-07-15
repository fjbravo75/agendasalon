from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST

from apps.booking.forms import (
    AppointmentCancelForm,
    AppointmentSearchForm,
    AvailabilityRuleForm,
    BusinessClosureForm,
    PublicBookingForm,
    ServiceForm,
    WorkLineForm,
)
from apps.booking.models import (
    Appointment,
    AvailabilityRule,
    BusinessCalendarSettings,
    BusinessClosure,
    Service,
    WorkLine,
)
from apps.booking.public_booking_drafts import (
    clear_public_booking_receipt,
    clear_public_booking_draft,
    get_public_booking_receipt_appointment_id,
    get_public_booking_draft,
    public_booking_draft_form_data,
    save_public_booking_receipt,
    save_public_booking_draft,
)
from apps.booking.schedule_context import (
    build_schedule_management_context as _schedule_management_context,
    closure_type_label as _closure_type_label,
    weekday_label as _weekday_label,
)
from apps.booking.services import (
    AppointmentDraft,
    cancel_appointment,
    close_appointments,
    complete_appointment,
    confirm_appointment,
    mark_appointment_no_show,
)
from apps.booking.slot_engine import (
    CHANNEL_PUBLIC,
    get_booking_options,
    get_day_availability,
    get_month_availability,
    suggest_next_slots,
)
from apps.businesses.activity import record_business_activity
from apps.businesses.models import Business, BusinessActivityEvent
from apps.businesses.services import (
    get_business_public_image_url,
    get_business_visual_theme,
    get_primary_business_for_user,
)
from apps.customers.forms import ProfessionalClientQuickForm
from apps.customers.services import (
    get_bookable_client,
    get_bookable_clients,
    get_session_client_access,
)


@login_required
def professional_agenda(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    active_services = list(
        business.services.filter(is_active=True).order_by("display_order", "name", "pk")
    )
    try:
        slot_interval_minutes = business.calendar_settings.slot_interval_minutes
    except ObjectDoesNotExist:
        slot_interval_minutes = 15

    service_durations = {
        service.duration_minutes
        for service in active_services
        if service.duration_minutes % slot_interval_minutes == 0
    }
    duration_options = sorted(
        set(range(slot_interval_minutes, 4 * 60 + 1, slot_interval_minutes))
        | service_durations
    )
    if not duration_options:
        duration_options = [slot_interval_minutes]
    default_duration = min(service_durations) if service_durations else duration_options[0]

    appointment_url_template = reverse(
        "booking:professional_appointment_detail",
        args=[999999],
    ).replace("999999", "__appointment_id__")
    agenda_config = {
        "dayEndpoint": reverse("booking:professional_agenda_day_data"),
        "monthEndpoint": reverse("booking:professional_agenda_month_data"),
        "appointmentAssistantUrl": reverse("booking:appointment_assistant"),
        "appointmentUrlTemplate": appointment_url_template,
        "businessName": business.commercial_name,
        "professionalSummaryUrl": reverse("dashboards:professional_home"),
        "scheduleUrl": reverse("booking:professional_schedule"),
        "initialDate": timezone.localdate().isoformat(),
        "initialDuration": default_duration,
        "durationOptions": duration_options,
        "slotIntervalMinutes": slot_interval_minutes,
    }
    return render(
        request,
        "professional/agenda.html",
        {
            "business": business,
            "agenda_config": agenda_config,
        },
    )


@login_required
def appointment_assistant(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    quick_client_form = ProfessionalClientQuickForm(business=business)
    is_quick_client_post = request.method == "POST" and request.POST.get("action") == "quick_client"

    if is_quick_client_post:
        quick_client_form = ProfessionalClientQuickForm(request.POST, business=business)
        if quick_client_form.is_valid():
            try:
                business_client, created = quick_client_form.save(recorded_by=request.user)
            except ValidationError as exc:
                quick_client_form.add_error(None, exc)
            else:
                if created:
                    messages.success(request, f"Ficha creada para {business_client.full_name}.")
                else:
                    messages.success(request, f"{business_client.full_name} ya estaba en clientes.")
                return redirect(_appointment_assistant_url_with_client(request.POST, business_client.id))
        form = None
    elif request.method == "POST":
        form = AppointmentSearchForm(request.POST, business=business)
        if form.is_valid():
            try:
                appointment = _confirm_professional_appointment(request, business, form)
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(
                    request,
                    f"Cita confirmada para {appointment.business_client.full_name}.",
                )
                return redirect(
                    "booking:professional_appointment_detail",
                    appointment_id=appointment.id,
                )
    else:
        form = None

    if is_quick_client_post:
        search_data = request.GET
    else:
        search_data = request.POST if request.method == "POST" else request.GET
    active_lines = tuple(
        WorkLine.objects.filter(
            business=business,
            is_active=True,
        ).order_by("display_order", "line_number", "pk")
    )
    initial = {
        "target_date": timezone.localdate(),
        "manual_channel": Appointment.ManualChannel.PHONE,
    }
    if request.method == "GET" and search_data:
        search_data = search_data.copy()
        search_data.setdefault("manual_channel", initial["manual_channel"])
        search_data.setdefault("target_date", initial["target_date"].isoformat())
    if form is None:
        form = AppointmentSearchForm(
            search_data or None,
            business=business,
            initial=initial,
        )

    context = {
        "business": business,
        "form": form,
        "available_services": tuple(form.fields["services"].queryset),
        "selected_service_ids": _selected_service_ids(form.data if form.is_bound else None),
        "active_lines": active_lines,
        "has_search": bool(search_data),
        "search_is_valid": False,
        "line_boards": _line_boards(active_lines, {}, {}),
        "quick_client_form": quick_client_form,
    }

    if search_data and form.is_valid():
        target_date = form.cleaned_data["target_date"]
        duration_minutes = form.cleaned_data["final_duration_minutes"]
        day_availability = get_day_availability(
            business=business,
            target_date=target_date,
            duration_minutes=duration_minutes,
        )
        month_availability = get_month_availability(
            business=business,
            year=target_date.year,
            month=target_date.month,
            duration_minutes=duration_minutes,
        )
        suggestions = tuple()
        if not day_availability.has_slots:
            suggestions = suggest_next_slots(
                business=business,
                start_date=target_date,
                duration_minutes=duration_minutes,
                limit=3,
            )

        selected_slot = _selected_available_slot(search_data, day_availability)
        slot_was_selected = selected_slot is not None
        if selected_slot is None and day_availability.has_slots:
            selected_slot = day_availability.slots[0]
        recommended_slot = selected_slot or (suggestions[0] if suggestions else None)
        day_unavailable_message = {
            "festivo_nacional": "Este día es festivo nacional y la agenda está cerrada.",
            "cierre_negocio": "Hay un cierre completo registrado para este día.",
            "sin_horario": "No hay horario activo para este día.",
            "negocio_inactivo": "El negocio está pausado y no admite nuevas citas.",
            "sin_lineas_activas": "No hay líneas activas para asignar la cita.",
        }.get(
            day_availability.reason,
            f"No hay hueco suficiente para {duration_minutes} min este día.",
        )

        context.update(
            {
                "selected_client": form.cleaned_data["business_client"],
                "search_is_valid": True,
                "selected_services": tuple(form.cleaned_data["services"]),
                "selected_date": target_date,
                "calculated_duration_minutes": form.cleaned_data["calculated_duration_minutes"],
                "duration_minutes": duration_minutes,
                "duration_was_adjusted": (
                    duration_minutes != form.cleaned_data["calculated_duration_minutes"]
                ),
                "day_availability": day_availability,
                "day_unavailable_message": day_unavailable_message,
                "month_days": month_availability.days,
                "suggestions": suggestions,
                "recommended_slot": recommended_slot,
                "slot_was_selected": slot_was_selected,
                "confirm_payload": _confirm_payload(form.cleaned_data),
                "line_boards": _line_boards(
                    active_lines,
                    _appointments_by_line(business, active_lines, target_date),
                    day_availability.slots_by_line,
                ),
            }
        )

    return render(request, "professional/appointment_assistant.html", context)


@login_required
def professional_appointment_detail(request, appointment_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    appointment = _get_professional_appointment(business, appointment_id)
    context = _appointment_detail_context(
        business=business,
        appointment=appointment,
        cancel_form=AppointmentCancelForm(),
    )
    return render(request, "professional/appointments/detail.html", context)


@login_required
def professional_pending_appointments(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    appointments = list(
        business.appointments.select_related("business_client", "work_line")
        .filter(status=Appointment.Status.CONFIRMED, ends_at__lte=timezone.now())
        .order_by("ends_at", "pk")
    )
    for appointment in appointments:
        appointment.local_starts_at = timezone.localtime(appointment.starts_at)
        appointment.local_ends_at = timezone.localtime(appointment.ends_at)
    return render(
        request,
        "professional/appointments/pending.html",
        {"business": business, "appointments": appointments},
    )


@login_required
def professional_appointment_cancel(request, appointment_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    appointment = _get_professional_appointment(business, appointment_id)
    detail_url = reverse("booking:professional_appointment_detail", args=[appointment.id])
    if request.method != "POST":
        return redirect(detail_url)

    cancel_form = AppointmentCancelForm(request.POST)
    if cancel_form.is_valid():
        try:
            appointment = cancel_appointment(
                appointment,
                cancelled_by=request.user,
                reason=cancel_form.cleaned_data["cancellation_reason"],
            )
        except ValidationError as exc:
            cancel_form.add_error(None, exc)
        else:
            messages.success(request, "Cita cancelada sin perder la trazabilidad.")
            return redirect(detail_url)

    context = _appointment_detail_context(
        business=business,
        appointment=appointment,
        cancel_form=cancel_form,
    )
    return render(request, "professional/appointments/detail.html", context)


@login_required
def professional_appointment_complete(request, appointment_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    appointment = _get_professional_appointment(business, appointment_id)
    detail_url = reverse("booking:professional_appointment_detail", args=[appointment.id])
    if request.method == "POST":
        try:
            complete_appointment(appointment, completed_by=request.user)
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "Cita marcada como atendida y guardada en el historial.")
    return redirect(detail_url)


@login_required
def professional_appointment_no_show(request, appointment_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    appointment = _get_professional_appointment(business, appointment_id)
    detail_url = reverse("booking:professional_appointment_detail", args=[appointment.id])
    if request.method == "POST":
        try:
            mark_appointment_no_show(appointment, marked_by=request.user)
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            messages.success(request, "La cita queda registrada como no presentada.")
    return redirect(detail_url)


@login_required
def professional_appointments_bulk_close(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    return_url = (
        reverse("booking:professional_pending_appointments")
        if request.POST.get("return_to") == "pending"
        else reverse("dashboards:professional_home")
    )
    if request.method != "POST":
        return redirect("dashboards:professional_home")

    appointment_ids = request.POST.getlist("appointment_ids")
    outcome = request.POST.get("outcome", "")
    appointments = list(
        business.appointments.filter(
            pk__in=appointment_ids,
            status=Appointment.Status.CONFIRMED,
            ends_at__lte=timezone.now(),
        ).order_by("ends_at", "pk")
    )
    if not appointments:
        messages.error(request, "Selecciona al menos una cita pendiente de cierre.")
        return redirect(return_url)

    try:
        closed_count = close_appointments(
            appointments,
            outcome=outcome,
            closed_by=request.user,
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        result_label = "atendidas" if outcome == Appointment.Status.COMPLETED else "no presentadas"
        messages.success(request, f"{closed_count} citas quedan registradas como {result_label}.")
    return redirect(return_url)


@login_required
def professional_service_list(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    if request.method == "POST":
        service_form = ServiceForm(request.POST, business=business)
        if service_form.is_valid():
            service = service_form.save()
            _record_configuration_activity(
                request,
                business,
                BusinessActivityEvent.EventType.SERVICE_CREATED,
                f'Servicio "{service.name}" creado.',
                service,
                "service",
                {"is_active": service.is_active},
            )
            if service.is_active:
                messages.success(request, f"{service.name} queda disponible para nuevas citas.")
            else:
                messages.success(request, f"{service.name} queda guardado como servicio pausado.")
            return redirect("booking:professional_service_list")
    else:
        service_form = ServiceForm(business=business)

    context = _service_management_context(
        business=business,
        service_form=service_form,
        editing_service=None,
    )
    return render(request, "professional/services/list.html", context)


@login_required
def professional_service_edit(request, service_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    service = get_object_or_404(Service, pk=service_id, business=business)
    if request.method == "POST":
        service_form = ServiceForm(request.POST, business=business, instance=service)
        if service_form.is_valid():
            service = service_form.save()
            _record_configuration_activity(
                request,
                business,
                BusinessActivityEvent.EventType.SERVICE_UPDATED,
                f'Servicio "{service.name}" actualizado.',
                service,
                "service",
                {"fields": tuple(service_form.changed_data)},
            )
            messages.success(request, f"{service.name} se ha actualizado.")
            return redirect("booking:professional_service_list")
    else:
        service_form = ServiceForm(business=business, instance=service)

    context = _service_management_context(
        business=business,
        service_form=service_form,
        editing_service=service,
    )
    return render(request, "professional/services/list.html", context)


@login_required
def professional_service_toggle(request, service_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    service = get_object_or_404(Service, pk=service_id, business=business)
    if request.method == "POST":
        service.is_active = not service.is_active
        service.full_clean()
        service.save(update_fields=["is_active", "updated_at"])
        _record_configuration_activity(
            request,
            business,
            (
                BusinessActivityEvent.EventType.SERVICE_REACTIVATED
                if service.is_active
                else BusinessActivityEvent.EventType.SERVICE_PAUSED
            ),
            f'Servicio "{service.name}" {"reactivado" if service.is_active else "pausado"}.',
            service,
            "service",
            {"is_active": service.is_active},
        )
        if service.is_active:
            messages.success(request, f"{service.name} vuelve a estar disponible para reservar.")
        else:
            messages.success(request, f"{service.name} queda pausado para nuevas citas.")
    return redirect("booking:professional_service_list")


@login_required
def professional_schedule(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    availability_form = AvailabilityRuleForm(business=business, prefix="availability")
    closure_form = BusinessClosureForm(business=business, created_by=request.user, prefix="closure")
    work_line_form = _new_work_line_form(business)

    if request.method == "POST":
        form_kind = request.POST.get("form_kind")
        if form_kind == "availability":
            availability_form = AvailabilityRuleForm(request.POST, business=business, prefix="availability")
            if availability_form.is_valid():
                rule = availability_form.save()
                _record_configuration_activity(
                    request,
                    business,
                    BusinessActivityEvent.EventType.AVAILABILITY_CREATED,
                    f"Horario creado para {_weekday_label(rule.weekday)} de {rule.start_time:%H:%M} a {rule.end_time:%H:%M}.",
                    rule,
                    "availability_rule",
                    {"is_active": rule.is_active},
                )
                messages.success(
                    request,
                    f"Horario guardado para {_weekday_label(rule.weekday)}.",
                )
                return redirect("booking:professional_schedule")
        elif form_kind == "closure":
            closure_form = BusinessClosureForm(
                request.POST,
                business=business,
                created_by=request.user,
                prefix="closure",
            )
            if closure_form.is_valid():
                closure = closure_form.save()
                _record_configuration_activity(
                    request,
                    business,
                    BusinessActivityEvent.EventType.CLOSURE_CREATED,
                    f"{_closure_type_label(closure.closure_type)} añadido al calendario.",
                    closure,
                    "business_closure",
                    {"is_active": closure.is_active},
                )
                messages.success(request, f"{_closure_type_label(closure.closure_type)} añadido al calendario.")
                return redirect("booking:professional_schedule")
        elif form_kind == "work_line":
            work_line_form = WorkLineForm(request.POST, business=business)
            if work_line_form.is_valid():
                line = work_line_form.save()
                _record_configuration_activity(
                    request,
                    business,
                    BusinessActivityEvent.EventType.WORK_LINE_CREATED,
                    f'{line} creada en la capacidad del negocio.',
                    line,
                    "work_line",
                    {"is_active": line.is_active},
                )
                messages.success(request, f"{line} queda disponible en la capacidad del salón.")
                return redirect("booking:professional_schedule")
        else:
            messages.error(request, "No se ha podido reconocer el ajuste que quieres guardar.")

    context = _schedule_management_context(
        business=business,
        availability_form=availability_form,
        closure_form=closure_form,
        work_line_form=work_line_form,
        editing_availability=None,
        editing_closure=None,
        editing_work_line=None,
    )
    return render(request, "professional/schedule.html", context)


@login_required
@require_POST
def professional_national_holidays_update(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    requested_value = request.POST.get("apply_national_holidays")
    if requested_value not in {"true", "false"}:
        messages.error(request, "No se ha podido reconocer el ajuste de festivos nacionales.")
        return redirect("booking:professional_schedule")

    calendar_settings, _created = BusinessCalendarSettings.objects.get_or_create(
        business=business
    )
    should_apply = requested_value == "true"
    if calendar_settings.apply_national_holidays == should_apply:
        messages.info(request, "La aplicación de festivos nacionales no tenía cambios pendientes.")
    else:
        calendar_settings.apply_national_holidays = should_apply
        calendar_settings.save(update_fields=["apply_national_holidays"])
        _record_configuration_activity(
            request,
            business,
            (
                BusinessActivityEvent.EventType.NATIONAL_HOLIDAYS_ENABLED
                if should_apply
                else BusinessActivityEvent.EventType.NATIONAL_HOLIDAYS_DISABLED
            ),
            (
                "Los festivos nacionales pasan a cerrar la agenda."
                if should_apply
                else "Los festivos nacionales dejan de cerrar la agenda."
            ),
            calendar_settings,
            "business_calendar_settings",
            {"apply_national_holidays": should_apply},
        )
        messages.success(
            request,
            (
                "La agenda respetará los festivos nacionales sincronizados."
                if should_apply
                else "La agenda permanecerá abierta en festivos nacionales salvo cierre manual."
            ),
        )
    return redirect(f"{reverse('booking:professional_schedule')}#festivos-nacionales")


@login_required
def professional_availability_edit(request, rule_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    rule = get_object_or_404(AvailabilityRule, pk=rule_id, business=business)
    if request.method == "POST":
        availability_form = AvailabilityRuleForm(
            request.POST,
            business=business,
            instance=rule,
            prefix="availability",
        )
        if availability_form.is_valid():
            rule = availability_form.save()
            _record_configuration_activity(
                request,
                business,
                BusinessActivityEvent.EventType.AVAILABILITY_UPDATED,
                f"Horario actualizado para {_weekday_label(rule.weekday)} de {rule.start_time:%H:%M} a {rule.end_time:%H:%M}.",
                rule,
                "availability_rule",
                {"fields": tuple(availability_form.changed_data)},
            )
            messages.success(request, f"Horario actualizado para {_weekday_label(rule.weekday)}.")
            return redirect("booking:professional_schedule")
    else:
        availability_form = AvailabilityRuleForm(business=business, instance=rule, prefix="availability")

    context = _schedule_management_context(
        business=business,
        availability_form=availability_form,
        closure_form=BusinessClosureForm(business=business, created_by=request.user, prefix="closure"),
        work_line_form=_new_work_line_form(business),
        editing_availability=rule,
        editing_closure=None,
        editing_work_line=None,
    )
    return render(request, "professional/schedule.html", context)


@login_required
def professional_availability_toggle(request, rule_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    rule = get_object_or_404(AvailabilityRule, pk=rule_id, business=business)
    if request.method == "POST":
        rule.is_active = not rule.is_active
        try:
            rule.full_clean()
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            rule.save(update_fields=["is_active"])
            _record_configuration_activity(
                request,
                business,
                (
                    BusinessActivityEvent.EventType.AVAILABILITY_REACTIVATED
                    if rule.is_active
                    else BusinessActivityEvent.EventType.AVAILABILITY_PAUSED
                ),
                f"Horario de {_weekday_label(rule.weekday)} {'reactivado' if rule.is_active else 'pausado'}.",
                rule,
                "availability_rule",
                {"is_active": rule.is_active},
            )
            if rule.is_active:
                messages.success(request, "El tramo vuelve a estar disponible para calcular huecos.")
            else:
                messages.success(request, "El tramo queda pausado sin perder su historial.")
    return redirect("booking:professional_schedule")


@login_required
def professional_work_line_edit(request, line_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    line = get_object_or_404(WorkLine, pk=line_id, business=business)
    if request.method == "POST":
        work_line_form = WorkLineForm(request.POST, business=business, instance=line)
        if work_line_form.is_valid():
            line = work_line_form.save()
            _record_configuration_activity(
                request,
                business,
                BusinessActivityEvent.EventType.WORK_LINE_UPDATED,
                f"{line} actualizada.",
                line,
                "work_line",
                {"fields": tuple(work_line_form.changed_data)},
            )
            messages.success(request, f"{line} se ha actualizado.")
            return redirect("booking:professional_schedule")
    else:
        work_line_form = WorkLineForm(business=business, instance=line)

    context = _schedule_management_context(
        business=business,
        availability_form=AvailabilityRuleForm(business=business, prefix="availability"),
        closure_form=BusinessClosureForm(business=business, created_by=request.user, prefix="closure"),
        work_line_form=work_line_form,
        editing_availability=None,
        editing_closure=None,
        editing_work_line=line,
    )
    return render(request, "professional/schedule.html", context)


@login_required
def professional_work_line_toggle(request, line_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    line = get_object_or_404(WorkLine, pk=line_id, business=business)
    if request.method == "POST":
        if line.is_active and _work_line_has_future_confirmed_appointments(line):
            messages.error(
                request,
                "Esta línea tiene citas futuras confirmadas. Reubícalas o complétalas antes de pausarla.",
            )
        else:
            line.is_active = not line.is_active
            try:
                line.full_clean()
            except ValidationError as exc:
                messages.error(request, _validation_message(exc))
            else:
                line.save(update_fields=["is_active"])
                _record_configuration_activity(
                    request,
                    business,
                    (
                        BusinessActivityEvent.EventType.WORK_LINE_REACTIVATED
                        if line.is_active
                        else BusinessActivityEvent.EventType.WORK_LINE_PAUSED
                    ),
                    f"{line} {'reactivada' if line.is_active else 'pausada'}.",
                    line,
                    "work_line",
                    {"is_active": line.is_active},
                )
                if line.is_active:
                    messages.success(request, f"{line} vuelve a estar disponible para nuevas citas.")
                else:
                    messages.success(request, f"{line} queda pausada para nuevas citas.")
    return redirect("booking:professional_schedule")


@login_required
def professional_closure_edit(request, closure_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    closure = get_object_or_404(BusinessClosure, pk=closure_id, business=business)
    if request.method == "POST":
        closure_form = BusinessClosureForm(
            request.POST,
            business=business,
            created_by=request.user,
            instance=closure,
            prefix="closure",
        )
        if closure_form.is_valid():
            closure = closure_form.save()
            _record_configuration_activity(
                request,
                business,
                BusinessActivityEvent.EventType.CLOSURE_UPDATED,
                f"{_closure_type_label(closure.closure_type)} actualizado.",
                closure,
                "business_closure",
                {"fields": tuple(closure_form.changed_data)},
            )
            messages.success(request, f"{_closure_type_label(closure.closure_type)} actualizado.")
            return redirect("booking:professional_schedule")
    else:
        closure_form = BusinessClosureForm(
            business=business,
            created_by=request.user,
            instance=closure,
            prefix="closure",
        )

    context = _schedule_management_context(
        business=business,
        availability_form=AvailabilityRuleForm(business=business, prefix="availability"),
        closure_form=closure_form,
        work_line_form=_new_work_line_form(business),
        editing_availability=None,
        editing_closure=closure,
        editing_work_line=None,
    )
    return render(request, "professional/schedule.html", context)


@login_required
def professional_closure_toggle(request, closure_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    closure = get_object_or_404(BusinessClosure, pk=closure_id, business=business)
    if request.method == "POST":
        closure.is_active = not closure.is_active
        try:
            closure.full_clean()
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            closure.save(update_fields=["is_active", "updated_at"])
            _record_configuration_activity(
                request,
                business,
                (
                    BusinessActivityEvent.EventType.CLOSURE_REACTIVATED
                    if closure.is_active
                    else BusinessActivityEvent.EventType.CLOSURE_PAUSED
                ),
                f"{_closure_type_label(closure.closure_type)} {'reactivado' if closure.is_active else 'pausado'}.",
                closure,
                "business_closure",
                {"is_active": closure.is_active},
            )
            if closure.is_active:
                messages.success(request, "El cierre vuelve a aplicarse en el calendario.")
            else:
                messages.success(request, "El cierre queda pausado sin borrarlo.")
    return redirect("booking:professional_schedule")


def public_booking(request, slug):
    business = get_object_or_404(
        Business,
        slug=slug,
        is_active=True,
        public_booking_enabled=True,
    )
    client_access = get_session_client_access(request, business)
    action = request.POST.get("action") if request.method == "POST" else ""

    if action == "confirm_booking":
        return _confirm_public_booking_draft(request, business, client_access)

    if request.GET.get("confirm") == "1":
        return _render_public_booking_confirmation(request, business, client_access)

    require_slot = action == "choose_slot"
    search_data = request.POST if request.method == "POST" else request.GET
    if not search_data.getlist("services") and not search_data.get("target_date"):
        search_data = None
    form = PublicBookingForm(
        search_data,
        business=business,
        require_slot=require_slot,
        initial={"target_date": timezone.localdate()},
    )

    context = _public_booking_base_context(
        business=business,
        client_access=client_access,
        form=form,
        has_search=bool(search_data),
    )

    if form.is_valid():
        duration_minutes = form.cleaned_data["final_duration_minutes"]
        target_date = form.cleaned_data["target_date"]
        selected_services = tuple(form.cleaned_data["services"])
        options = get_booking_options(
            business=business,
            start_date=target_date,
            duration_minutes=duration_minutes,
            channel=CHANNEL_PUBLIC,
            days_ahead=30,
            limit=4,
        )

        context.update(
            {
                "search_is_valid": True,
                "selected_services": selected_services,
                "duration_minutes": duration_minutes,
                "target_date": target_date,
                "options": options,
                "recommended_slot": options[0] if options else None,
                "booking_progress_step": "time",
                **_public_price_summary(selected_services),
            }
        )

        if action == "choose_slot":
            save_public_booking_draft(request, business, form.cleaned_data)
            confirmation_url = _public_booking_confirmation_url(business)
            if client_access is None:
                login_url = reverse("customers:client_access", args=[business.slug])
                return redirect(f"{login_url}?{urlencode({'next': confirmation_url})}")
            return redirect(confirmation_url)

    return render(request, "public/booking.html", context)


def public_booking_receipt(request, slug):
    business = get_object_or_404(
        Business,
        slug=slug,
        is_active=True,
        public_booking_enabled=True,
    )
    client_access = get_session_client_access(request, business)
    receipt_url = reverse("public_booking_receipt", args=[business.slug])
    if client_access is None:
        login_url = reverse("customers:client_access", args=[business.slug])
        return redirect(f"{login_url}?{urlencode({'next': receipt_url})}")

    appointment_id = get_public_booking_receipt_appointment_id(request, business)
    if appointment_id is None:
        messages.info(request, "No hay una confirmación reciente para mostrar.")
        return redirect("public_booking", slug=business.slug)

    appointment = (
        Appointment.objects.filter(
            pk=appointment_id,
            business=business,
            requested_by_client_access=client_access,
        )
        .select_related("business_client")
        .prefetch_related("appointment_services", "outbound_emails")
        .first()
    )
    if appointment is None:
        clear_public_booking_receipt(request, business)
        messages.error(request, "No podemos mostrar esa confirmación desde esta cuenta.")
        return redirect("public_booking", slug=business.slug)

    appointment_services = tuple(appointment.appointment_services.all())
    priced_services = [
        item for item in appointment_services if item.price_amount_snapshot is not None
    ]
    confirmation_email = next(
        (
            email
            for email in appointment.outbound_emails.all()
            if email.kind == "appointment_confirmation"
        ),
        None,
    )
    return render(
        request,
        "public/booking_receipt.html",
        {
            "business": business,
            "client_access": client_access,
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
            "appointment": appointment,
            "appointment_services": appointment_services,
            "confirmation_email": confirmation_email,
            "total_price": sum(
                (item.price_amount_snapshot for item in priced_services),
                Decimal("0.00"),
            ),
            "has_priced_services": bool(priced_services),
            "has_unpriced_services": len(priced_services) != len(appointment_services),
        },
    )


def _confirm_public_booking_draft(request, business, client_access):
    draft = get_public_booking_draft(request, business)
    if draft is None:
        messages.error(request, "La selección ha caducado. Elige de nuevo los servicios y la hora.")
        return redirect("public_booking", slug=business.slug)

    if client_access is None:
        confirmation_url = _public_booking_confirmation_url(business)
        login_url = reverse("customers:client_access", args=[business.slug])
        return redirect(f"{login_url}?{urlencode({'next': confirmation_url})}")

    form = PublicBookingForm(
        public_booking_draft_form_data(draft),
        business=business,
        require_slot=True,
    )
    if not form.is_valid():
        clear_public_booking_draft(request, business)
        messages.error(request, "La selección ya no es válida. Revisa los servicios y elige otra hora.")
        return redirect(_public_booking_search_url(business, draft))

    try:
        beneficiary = get_bookable_client(
            client_access,
            request.POST.get("business_client") or client_access.business_client_id,
        )
        if beneficiary is None:
            raise ValidationError("Ya no tienes permiso para reservar para esa persona.")
        appointment = _confirm_public_appointment(business, client_access, beneficiary, form)
    except (ValidationError, WorkLine.DoesNotExist) as exc:
        clear_public_booking_draft(request, business)
        messages.error(request, _validation_message(exc))
        return redirect(_public_booking_search_url(business, draft))

    clear_public_booking_draft(request, business)
    save_public_booking_receipt(request, business, appointment)
    messages.success(
        request,
        f"Cita confirmada para {appointment.business_client.full_name}.",
    )
    return redirect("public_booking_receipt", slug=business.slug)


def _render_public_booking_confirmation(request, business, client_access):
    draft = get_public_booking_draft(request, business)
    if draft is None:
        messages.error(request, "La selección ha caducado. Elige de nuevo los servicios y la hora.")
        return redirect("public_booking", slug=business.slug)

    if client_access is None:
        confirmation_url = _public_booking_confirmation_url(business)
        login_url = reverse("customers:client_access", args=[business.slug])
        return redirect(f"{login_url}?{urlencode({'next': confirmation_url})}")

    form = PublicBookingForm(
        public_booking_draft_form_data(draft),
        business=business,
        require_slot=True,
    )
    if not form.is_valid():
        clear_public_booking_draft(request, business)
        messages.error(request, "La selección ya no es válida. Revisa los servicios y elige otra hora.")
        return redirect(_public_booking_search_url(business, draft))

    duration_minutes = form.cleaned_data["final_duration_minutes"]
    target_date = form.cleaned_data["target_date"]
    day_availability = get_day_availability(
        business=business,
        target_date=target_date,
        duration_minutes=duration_minutes,
    )
    selected_slot = _selected_available_slot(draft, day_availability)
    if selected_slot is None:
        clear_public_booking_draft(request, business)
        messages.error(
            request,
            "Esa hora acaba de ocuparse. Te mostramos las siguientes opciones disponibles.",
        )
        return redirect(_public_booking_search_url(business, draft))

    selected_services = tuple(form.cleaned_data["services"])
    bookable_clients = tuple(get_bookable_clients(client_access))
    context = _public_booking_base_context(
        business=business,
        client_access=client_access,
        form=form,
        has_search=True,
    )
    context.update(
        {
            "confirmation_pending": True,
            "search_is_valid": True,
            "selected_services": selected_services,
            "duration_minutes": duration_minutes,
            "target_date": target_date,
            "selected_slot": selected_slot,
            "booking_progress_step": "confirm",
            "change_search_url": _public_booking_search_url(business, draft),
            "bookable_clients": bookable_clients,
            "selected_business_client": client_access.business_client,
            **_public_price_summary(selected_services),
        }
    )
    return render(request, "public/booking.html", context)


def _public_booking_base_context(*, business, client_access, form, has_search):
    data = form.data if form.is_bound else None
    return {
        "business": business,
        "client_access": client_access,
        "client_auth_theme": get_business_visual_theme(business),
        "client_auth_image_url": get_business_public_image_url(business),
        "form": form,
        "available_services": tuple(form.fields["services"].queryset),
        "selected_service_ids": _selected_service_ids(data),
        "has_search": has_search,
        "search_is_valid": False,
        "confirmation_pending": False,
        "booking_progress_step": "services",
        "public_booking_url": reverse("public_booking", args=[business.slug]),
    }


def _selected_service_ids(data):
    if not data:
        return tuple()
    values = data.getlist("services") if hasattr(data, "getlist") else data.get("services", [])
    if isinstance(values, (str, int)):
        values = [values]
    selected_ids = []
    for value in values:
        try:
            selected_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(selected_ids)


def _public_price_summary(services):
    priced_services = [service for service in services if service.price_amount is not None]
    total_price = sum(
        (service.price_amount for service in priced_services),
        Decimal("0.00"),
    )
    return {
        "total_price": total_price,
        "has_priced_services": bool(priced_services),
        "has_unpriced_services": len(priced_services) != len(services),
    }


def _public_booking_confirmation_url(business):
    return f"{reverse('public_booking', args=[business.slug])}?confirm=1"


def _public_booking_search_url(business, draft):
    params = [("services", service_id) for service_id in draft.get("service_ids", [])]
    if draft.get("target_date"):
        params.append(("target_date", draft["target_date"]))
    base_url = reverse("public_booking", args=[business.slug])
    return f"{base_url}?{urlencode(params)}" if params else base_url


def _get_professional_appointment(business, appointment_id):
    return get_object_or_404(
        Appointment.objects.filter(business=business)
        .select_related(
            "business_client",
            "work_line",
            "created_by",
            "cancelled_by",
            "completed_by",
            "no_show_marked_by",
        )
        .prefetch_related("appointment_services"),
        pk=appointment_id,
    )


def _appointment_detail_context(business, appointment, cancel_form):
    is_confirmed = appointment.status == Appointment.Status.CONFIRMED
    has_started = appointment.starts_at <= timezone.now()
    can_complete = is_confirmed and has_started
    return {
        "business": business,
        "appointment": appointment,
        "appointment_services": tuple(appointment.appointment_services.all()),
        "appointment_emails": tuple(appointment.outbound_emails.order_by("scheduled_for", "pk")),
        "cancel_form": cancel_form,
        "can_cancel": is_confirmed,
        "can_complete": can_complete,
        "can_mark_no_show": can_complete,
        "is_pending_closure": appointment.is_pending_closure(),
        "complete_blocked_reason": (
            "La cita todavía no ha empezado."
            if is_confirmed and not can_complete
            else ""
        ),
    }


def _service_management_context(business, service_form, editing_service):
    services = tuple(
        Service.objects.filter(business=business)
        .annotate(appointments_total=Count("appointment_services", distinct=True))
        .order_by("-is_active", "display_order", "name", "pk")
    )
    active_services = [service for service in services if service.is_active]
    average_duration = 0
    if active_services:
        average_duration = round(
            sum(service.duration_minutes for service in active_services)
            / len(active_services)
        )
    priced_services_count = sum(
        1 for service in active_services if service.price_amount is not None
    )

    return {
        "business": business,
        "services": services,
        "service_form": service_form,
        "editing_service": editing_service,
        "active_services_count": len(active_services),
        "paused_services_count": len(services) - len(active_services),
        "average_duration": average_duration,
        "priced_services_count": priced_services_count,
    }


def _new_work_line_form(business):
    used_line_numbers = set(business.work_lines.values_list("line_number", flat=True))
    next_available_line_number = next(
        (number for number in (1, 2, 3) if number not in used_line_numbers),
        None,
    )
    if next_available_line_number is None:
        return None
    return WorkLineForm(
        business=business,
        initial={
            "line_number": next_available_line_number,
            "display_order": next_available_line_number,
            "is_active": True,
        },
    )


def _work_line_has_future_confirmed_appointments(line):
    return line.appointments.filter(
        status=Appointment.Status.CONFIRMED,
        starts_at__gte=timezone.now(),
    ).exists()


def _validation_message(exc):
    if hasattr(exc, "messages"):
        return " ".join(exc.messages)
    return str(exc)


def _record_configuration_activity(
    request,
    business,
    event_type,
    summary,
    entity,
    entity_type,
    changes=None,
):
    return record_business_activity(
        business=business,
        category=BusinessActivityEvent.Category.CONFIGURATION,
        event_type=event_type,
        origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
        summary=summary,
        actor=request.user,
        entity=entity,
        entity_type=entity_type,
        changes=changes,
    )


def _appointments_by_line(business, active_lines, target_date):
    tz = ZoneInfo(settings.TIME_ZONE)
    day_start = datetime.combine(target_date, datetime.min.time(), tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    grouped = defaultdict(list)

    appointments = (
        Appointment.objects.filter(
            business=business,
            work_line__in=active_lines,
            starts_at__lt=day_end,
            ends_at__gt=day_start,
        )
        .select_related("business_client", "work_line")
        .prefetch_related("appointment_services")
        .order_by("starts_at", "pk")
    )

    for appointment in appointments:
        appointment.starts_at = timezone.localtime(appointment.starts_at, tz)
        appointment.ends_at = timezone.localtime(appointment.ends_at, tz)
        grouped[appointment.work_line_id].append(appointment)

    return {line.id: tuple(grouped[line.id]) for line in active_lines}


def _line_boards(active_lines, appointments_by_line, slots_by_line):
    return tuple(
        {
            "line": line,
            "appointments": appointments_by_line.get(line.id, tuple()),
            "slots": slots_by_line.get(line.id, tuple()),
        }
        for line in active_lines
    )


def _confirm_professional_appointment(request, business, form):
    starts_at = _selected_starts_at(request.POST)
    work_line_id = request.POST.get("selected_work_line_id")
    if not work_line_id:
        raise ValidationError("Elige un hueco para confirmar la cita.")

    requested_by_contact = form.cleaned_data.get("requested_by_contact")
    return confirm_appointment(
        AppointmentDraft(
            business=business,
            business_client=form.cleaned_data["business_client"],
            services=tuple(form.cleaned_data["services"]),
            work_line_id=int(work_line_id),
            starts_at=starts_at,
            duration_minutes=form.cleaned_data["final_duration_minutes"],
            duration_adjustment_reason=(
                form.cleaned_data.get("duration_adjustment_reason") or ""
            ).strip(),
            channel=form.cleaned_data["manual_channel"],
            created_by=request.user,
            requested_by_name=(
                requested_by_contact.full_name
                if requested_by_contact
                else form.cleaned_data["business_client"].full_name
            ),
            requested_by_relationship=(
                requested_by_contact.get_relationship_label_display()
                if requested_by_contact
                else "Cliente"
            ),
        )
    )


def _confirm_public_appointment(business, client_access, business_client, form):
    grant = client_access.booking_grants.filter(
        business_client=business_client,
        is_active=True,
    ).first()
    return confirm_appointment(
        AppointmentDraft(
            business=business,
            business_client=business_client,
            services=tuple(form.cleaned_data["services"]),
            work_line_id=form.cleaned_data["selected_work_line_id"],
            starts_at=form.cleaned_data["selected_starts_at"],
            duration_minutes=form.cleaned_data["final_duration_minutes"],
            channel=Appointment.ManualChannel.PUBLIC_WEB,
            requested_by_client_access=client_access,
            requested_by_name=client_access.business_client.full_name,
            requested_by_relationship=(
                grant.get_relationship_label_display()
                if grant
                else "Es su propia ficha"
            ),
        )
    )


def _selected_starts_at(data):
    value = (data.get("selected_starts_at") or "").strip()
    if not value:
        raise ValidationError("Elige un hueco para confirmar la cita.")
    starts_at = parse_datetime(value)
    if starts_at is None:
        raise ValidationError("El hueco seleccionado no es válido.")
    return starts_at


def _selected_available_slot(data, day_availability):
    work_line_id = str(data.get("selected_work_line_id") or "").strip()
    starts_at_value = str(data.get("selected_starts_at") or "").strip()
    if not work_line_id or not starts_at_value:
        return None

    try:
        parsed_work_line_id = int(work_line_id)
    except (TypeError, ValueError):
        return None

    parsed_starts_at = parse_datetime(starts_at_value)
    if parsed_starts_at is None:
        return None
    if timezone.is_naive(parsed_starts_at):
        parsed_starts_at = timezone.make_aware(parsed_starts_at)

    return next(
        (
            slot
            for slot in day_availability.slots
            if slot.work_line_id == parsed_work_line_id and slot.starts_at == parsed_starts_at
        ),
        None,
    )


def _confirm_payload(cleaned_data):
    requested_by_contact = cleaned_data.get("requested_by_contact")
    return {
        "business_client": cleaned_data["business_client"].id,
        "manual_channel": cleaned_data["manual_channel"],
        "requested_by_contact": (
            f"contact:{requested_by_contact.id}" if requested_by_contact else "self"
        ),
        "services": [service.id for service in cleaned_data["services"]],
        "target_date": cleaned_data["target_date"].isoformat(),
        "adjusted_duration_minutes": cleaned_data.get("adjusted_duration_minutes") or "",
        "duration_adjustment_reason": cleaned_data.get("duration_adjustment_reason") or "",
    }


def _appointment_assistant_url_with_client(data, business_client_id):
    params = [("business_client", business_client_id)]
    for field_name in (
        "manual_channel",
        "target_date",
        "adjusted_duration_minutes",
        "duration_adjustment_reason",
    ):
        value = (data.get(field_name) or "").strip()
        if value:
            params.append((field_name, value))
    for service_id in data.getlist("services"):
        if service_id:
            params.append(("services", service_id))
    query = urlencode(params, doseq=True)
    return f"{reverse('booking:appointment_assistant')}?{query}"

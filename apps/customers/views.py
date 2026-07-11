from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Min, Q
from django.forms import ValidationError as FormValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.booking.models import Appointment
from apps.booking.public_booking_drafts import get_public_booking_draft
from apps.businesses.models import Business
from apps.businesses.activity import record_business_activity
from apps.businesses.models import BusinessActivityEvent
from apps.businesses.services import (
    get_business_public_image_url,
    get_business_visual_theme,
    get_primary_business_for_user,
)
from apps.customers.forms import (
    ClientInvitationActivationForm,
    ClientLoginForm,
    ClientRegistrationForm,
    ProfessionalAuthorizedContactForm,
    ProfessionalClientEditForm,
    ProfessionalClientQuickForm,
)
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccessInvitation,
    BusinessClientAuthorizedContact,
)
from apps.customers.services import (
    activate_claimed_invitation,
    create_client_access_invitation,
    find_available_invitation,
    get_claimed_invitation,
    get_session_client_access,
    login_client_access,
    logout_client_access,
    revoke_client_access_invitation,
    set_authorized_contact_active,
    set_client_access_active,
    set_professional_client_active,
    store_invitation_claim,
)
from apps.core.security_throttle import (
    THROTTLE_MESSAGE,
    clear_failed_attempts,
    is_throttled,
    phone_throttle_key,
    record_failed_attempt,
    request_ip,
)


@login_required
def professional_client_list(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    quick_form = ProfessionalClientQuickForm(business=business)
    if request.method == "POST":
        quick_form = ProfessionalClientQuickForm(request.POST, business=business)
        if quick_form.is_valid():
            try:
                client, created = quick_form.save()
            except FormValidationError as exc:
                quick_form.add_error(None, exc)
            else:
                if created:
                    messages.success(request, f"Ficha creada para {client.full_name}.")
                else:
                    messages.success(request, f"{client.full_name} ya estaba en clientes.")
                return redirect("customers:professional_client_detail", client_id=client.id)

    search = (request.GET.get("q") or "").strip()
    status_filter = request.GET.get("status") or "active"
    if status_filter not in {"active", "inactive", "all"}:
        status_filter = "active"

    clients = BusinessClient.objects.filter(business=business)
    if status_filter == "active":
        clients = clients.filter(is_active=True)
    elif status_filter == "inactive":
        clients = clients.filter(is_active=False)
    if search:
        clients = clients.filter(
            Q(full_name__icontains=search)
            | Q(phone__icontains=search)
            | Q(phone_normalized__icontains=search)
        )

    now = timezone.now()
    clients = (
        clients.annotate(
            appointments_total=Count("appointments", distinct=True),
            last_appointment_at=Max(
                "appointments__starts_at",
                filter=Q(appointments__starts_at__lt=now),
            ),
            next_appointment_at=Min(
                "appointments__starts_at",
                filter=Q(
                    appointments__starts_at__gte=now,
                    appointments__status=Appointment.Status.CONFIRMED,
                ),
            ),
        )
        .order_by("full_name", "pk")
    )

    return render(
        request,
        "professional/clients/list.html",
        {
            "business": business,
            "clients": clients,
            "search": search,
            "status_filter": status_filter,
            "quick_form": quick_form,
            "clients_count": clients.count(),
            "active_clients_count": BusinessClient.objects.filter(
                business=business,
                is_active=True,
            ).count(),
            "inactive_clients_count": BusinessClient.objects.filter(
                business=business,
                is_active=False,
            ).count(),
            "all_clients_count": BusinessClient.objects.filter(business=business).count(),
        },
    )


@login_required
def professional_client_detail(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    business_client = _get_professional_client(business, client_id)
    return render(
        request,
        "professional/clients/detail.html",
        _professional_client_context(business, business_client),
    )


@login_required
def professional_client_edit(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    business_client = _get_professional_client(business, client_id)
    edit_form = ProfessionalClientEditForm(
        request.POST or None,
        business=business,
        instance=business_client,
    )
    if request.method == "POST" and edit_form.is_valid():
        try:
            business_client = edit_form.save()
        except ValidationError as exc:
            edit_form.add_error(None, exc)
        else:
            messages.success(request, f"Datos actualizados para {business_client.full_name}.")
            return redirect("customers:professional_client_detail", client_id=business_client.id)

    return render(
        request,
        "professional/clients/edit.html",
        {
            **_professional_client_context(business, business_client),
            "edit_form": edit_form,
        },
    )


@login_required
@require_POST
def professional_client_toggle(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    business_client = _get_professional_client(business, client_id)
    target_state = not business_client.is_active
    try:
        set_professional_client_active(client=business_client, is_active=target_state)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        if target_state:
            messages.success(request, f"La ficha de {business_client.full_name} vuelve a estar activa.")
        else:
            messages.success(request, f"La ficha de {business_client.full_name} queda pausada.")
    return redirect("customers:professional_client_detail", client_id=business_client.id)


@login_required
def professional_contact_create(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    business_client = _get_professional_client(business, client_id)
    return _professional_contact_form_view(
        request,
        business=business,
        business_client=business_client,
        contact=None,
    )


@login_required
def professional_contact_edit(request, client_id, contact_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    business_client = _get_professional_client(business, client_id)
    contact = get_object_or_404(
        BusinessClientAuthorizedContact,
        id=contact_id,
        business=business,
        business_client=business_client,
    )
    return _professional_contact_form_view(
        request,
        business=business,
        business_client=business_client,
        contact=contact,
    )


@login_required
@require_POST
def professional_contact_toggle(request, client_id, contact_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    business_client = _get_professional_client(business, client_id)
    contact = get_object_or_404(
        BusinessClientAuthorizedContact,
        id=contact_id,
        business=business,
        business_client=business_client,
    )
    target_state = not contact.is_active
    try:
        contact, demoted = set_authorized_contact_active(contact=contact, is_active=target_state)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        if target_state and demoted:
            messages.success(
                request,
                f"{contact.full_name} vuelve a estar autorizado. Revisa quién debe ser el contacto principal.",
            )
        elif target_state:
            messages.success(request, f"{contact.full_name} vuelve a estar autorizado.")
        else:
            messages.success(request, f"{contact.full_name} queda pausado como persona autorizada.")
    return redirect("customers:professional_client_detail", client_id=business_client.id)


@login_required
@require_POST
def professional_client_access_toggle(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    business_client = _get_professional_client(business, client_id)
    client_access = getattr(business_client, "access", None)
    if client_access is None:
        messages.error(request, "Esta ficha todavía no tiene una cuenta online.")
    else:
        target_state = not client_access.is_active
        try:
            set_client_access_active(access=client_access, is_active=target_state)
        except ValidationError as exc:
            messages.error(request, _validation_message(exc))
        else:
            if target_state:
                messages.success(request, "La cuenta online vuelve a estar activa.")
            else:
                messages.success(request, "La cuenta online queda pausada.")
    return redirect("customers:professional_client_detail", client_id=business_client.id)


@login_required
@require_POST
def professional_client_invitation_create(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    business_client = _get_professional_client(business, client_id)
    try:
        invitation, raw_token = create_client_access_invitation(
            business=business,
            business_client=business_client,
            created_by=request.user,
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        return redirect("customers:professional_client_detail", client_id=business_client.id)

    invitation_url = request.build_absolute_uri(
        reverse(
            "customers:client_invitation_claim",
            args=[business.slug, invitation.id, raw_token],
        )
    )
    record_business_activity(
        business=business,
        category=BusinessActivityEvent.Category.ACCESS,
        event_type=BusinessActivityEvent.EventType.CLIENT_INVITATION_CREATED,
        origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
        summary=f"Invitación de cuenta online creada para {business_client.full_name}.",
        actor=request.user,
        entity=business_client,
        entity_type="business_client",
        changes={"expires_at": invitation.expires_at.isoformat()},
    )
    response = render(
        request,
        "professional/clients/invitation_created.html",
        {
            "business": business,
            "business_client": business_client,
            "invitation": invitation,
            "invitation_url": invitation_url,
        },
    )
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def professional_client_invitation_revoke(request, client_id, invitation_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    business_client = _get_professional_client(business, client_id)
    invitation = get_object_or_404(
        BusinessClientAccessInvitation,
        id=invitation_id,
        business=business,
        business_client=business_client,
    )
    try:
        revoke_client_access_invitation(invitation=invitation)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        record_business_activity(
            business=business,
            category=BusinessActivityEvent.Category.ACCESS,
            event_type=BusinessActivityEvent.EventType.CLIENT_INVITATION_REVOKED,
            origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
            summary=f"Invitación de cuenta online revocada para {business_client.full_name}.",
            actor=request.user,
            entity=business_client,
            entity_type="business_client",
        )
        messages.success(request, "La invitación ha quedado revocada.")
    return redirect("customers:professional_client_detail", client_id=business_client.id)


def _professional_client_context(business, business_client):
    now = timezone.now()
    appointments = (
        business_client.appointments.select_related("work_line")
        .prefetch_related("appointment_services")
        .order_by("-starts_at", "-pk")
    )
    upcoming_appointments = appointments.filter(
        status=Appointment.Status.CONFIRMED,
        starts_at__gte=now,
    ).order_by("starts_at", "pk")[:5]
    history_appointments = list(
        appointments.exclude(
            status=Appointment.Status.CONFIRMED,
            starts_at__gte=now,
        )
        .order_by("-starts_at", "-pk")[:12]
    )
    for appointment in history_appointments:
        appointment.operational_status_label = (
            "Pendiente de cierre"
            if appointment.is_pending_closure()
            else appointment.get_status_display()
        )

    client_access = getattr(business_client, "access", None)
    active_invitation = business_client.access_invitations.filter(
        used_at__isnull=True,
        revoked_at__isnull=True,
        expires_at__gt=now,
    ).order_by("-created_at").first()
    return {
        "business": business,
        "business_client": business_client,
        "upcoming_appointments": upcoming_appointments,
        "history_appointments": history_appointments,
        "authorized_contacts": business_client.authorized_contacts.all().order_by(
            "-is_active",
            "-is_primary_contact",
            "full_name",
            "pk",
        ),
        "active_contacts_count": business_client.authorized_contacts.filter(is_active=True).count(),
        "client_access": client_access,
        "active_invitation": active_invitation,
        "has_client_access": client_access is not None and client_access.is_active,
        "pending_confirmed_count": business_client.appointments.filter(
            status=Appointment.Status.CONFIRMED,
            ends_at__gt=now,
        ).count(),
    }


def _professional_contact_form_view(request, *, business, business_client, contact):
    contact_form = ProfessionalAuthorizedContactForm(
        request.POST or None,
        business=business,
        business_client=business_client,
        instance=contact,
    )
    if request.method == "POST" and contact_form.is_valid():
        try:
            contact = contact_form.save()
        except ValidationError as exc:
            contact_form.add_error(None, exc)
        else:
            messages.success(request, f"Persona autorizada guardada: {contact.full_name}.")
            return redirect("customers:professional_client_detail", client_id=business_client.id)

    return render(
        request,
        "professional/clients/contact_form.html",
        {
            **_professional_client_context(business, business_client),
            "contact_form": contact_form,
            "editing_contact": contact,
        },
    )


def _get_professional_client(business, client_id):
    return get_object_or_404(
        BusinessClient.objects.select_related("access")
        .prefetch_related("authorized_contacts")
        .filter(business=business),
        id=client_id,
    )


def _validation_message(exc):
    messages_list = getattr(exc, "messages", None)
    if messages_list:
        return " ".join(messages_list)
    return str(exc)


def client_access(request, slug):
    business = get_object_or_404(
        Business,
        slug=slug,
        is_active=True,
        public_booking_enabled=True,
    )
    next_url = _safe_next_url(request, business)
    auth_theme = get_business_visual_theme(business)
    has_pending_booking = get_public_booking_draft(request, business) is not None

    if get_session_client_access(request, business):
        return redirect(next_url)

    login_form = ClientLoginForm(business=business)
    response_status = 200

    if request.method == "POST":
        subject_key = f"{business.id}:{phone_throttle_key(request.POST.get('phone', ''))}"
        ip_key = request_ip(request)
        if is_throttled(scope="client_login_subject", key=subject_key) or is_throttled(
            scope="client_login_ip", key=ip_key
        ):
            login_form = ClientLoginForm(
                request.POST,
                business=business,
                skip_authentication=True,
            )
            login_form.is_valid()
            login_form.add_error(None, THROTTLE_MESSAGE)
            response_status = 429
        else:
            login_form = ClientLoginForm(request.POST, business=business)
            if login_form.is_valid():
                clear_failed_attempts(scope="client_login_subject", key=subject_key)
                login_client_access(request, login_form.client_access)
                messages.success(request, "Has entrado en tu zona de reservas.")
                return redirect(next_url)
            subject_blocked = record_failed_attempt(
                scope="client_login_subject",
                key=subject_key,
                limit=5,
                window_seconds=15 * 60,
            )
            ip_blocked = record_failed_attempt(
                scope="client_login_ip",
                key=ip_key,
                limit=30,
                window_seconds=15 * 60,
            )
            if subject_blocked or ip_blocked:
                login_form.add_error(None, THROTTLE_MESSAGE)
                response_status = 429

    return render(
        request,
        "customers/client_access.html",
        {
            "business": business,
            "login_form": login_form,
            "next_url": next_url,
            "client_auth_theme": auth_theme,
            "client_auth_image_url": get_business_public_image_url(business),
            "has_pending_booking": has_pending_booking,
        },
        status=response_status,
    )


def client_invitation_claim(request, slug, invitation_id, token):
    business = get_object_or_404(
        Business,
        slug=slug,
        is_active=True,
        public_booking_enabled=True,
    )
    ip_key = request_ip(request)
    if is_throttled(scope="client_invitation_claim_ip", key=ip_key):
        return _invitation_unavailable_response(request, business, status=429)

    invitation = find_available_invitation(
        invitation_id=invitation_id,
        raw_token=token,
        business=business,
    )
    if invitation is None:
        blocked = record_failed_attempt(
            scope="client_invitation_claim_ip",
            key=ip_key,
            limit=20,
            window_seconds=15 * 60,
        )
        return _invitation_unavailable_response(
            request,
            business,
            status=429 if blocked else 410,
        )

    store_invitation_claim(request, invitation)
    response = redirect("customers:client_invitation_activate", slug=business.slug)
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


def client_invitation_activate(request, slug):
    business = get_object_or_404(
        Business,
        slug=slug,
        is_active=True,
        public_booking_enabled=True,
    )
    invitation = get_claimed_invitation(request, business)
    if invitation is None:
        return _invitation_unavailable_response(request, business)

    activation_form = ClientInvitationActivationForm(request.POST or None)
    if request.method == "POST" and activation_form.is_valid():
        try:
            access, used_invitation = activate_claimed_invitation(
                request=request,
                business=business,
                password=activation_form.cleaned_data["password"],
            )
        except ValidationError:
            return _invitation_unavailable_response(request, business)
        login_client_access(request, access)
        record_business_activity(
            business=business,
            category=BusinessActivityEvent.Category.ACCESS,
            event_type=BusinessActivityEvent.EventType.CLIENT_ACCESS_ACTIVATED,
            origin=BusinessActivityEvent.Origin.PUBLIC_WEB,
            summary=f"Cuenta online activada para {access.business_client.full_name}.",
            actor_type=BusinessActivityEvent.ActorType.CUSTOMER,
            actor_label=access.business_client.full_name,
            entity=access.business_client,
            entity_type="business_client",
            changes={"invitation_id": str(used_invitation.id)},
        )
        messages.success(request, "Cuenta activada. Ya puedes reservar tu cita.")
        return redirect("public_booking", slug=business.slug)

    response = render(
        request,
        "customers/client_invitation_activate.html",
        {
            "business": business,
            "business_client": invitation.business_client,
            "activation_form": activation_form,
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
        },
    )
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


def _invitation_unavailable_response(request, business, status=410):
    response = render(
        request,
        "customers/client_invitation_unavailable.html",
        {
            "business": business,
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
            "rate_limited": status == 429,
        },
        status=status,
    )
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response


def client_register(request, slug):
    business = get_object_or_404(
        Business,
        slug=slug,
        is_active=True,
        public_booking_enabled=True,
    )
    next_url = _safe_next_url(request, business)
    auth_theme = get_business_visual_theme(business)
    has_pending_booking = get_public_booking_draft(request, business) is not None

    if get_session_client_access(request, business):
        return redirect(next_url)

    registration_form = ClientRegistrationForm(business=business)

    if request.method == "POST":
        registration_form = ClientRegistrationForm(request.POST, business=business)
        if registration_form.is_valid():
            try:
                access = registration_form.save()
            except FormValidationError as exc:
                registration_form.add_error(None, exc)
            else:
                login_client_access(request, access)
                messages.success(request, "Cuenta creada. Ya puedes reservar tu cita.")
                return redirect(next_url)

    return render(
        request,
        "customers/client_register.html",
        {
            "business": business,
            "registration_form": registration_form,
            "next_url": next_url,
            "client_auth_theme": auth_theme,
            "client_auth_image_url": get_business_public_image_url(business),
            "has_pending_booking": has_pending_booking,
        },
    )


@require_POST
def client_logout(request, slug):
    business = get_object_or_404(Business, slug=slug, is_active=True)
    logout_client_access(request)
    messages.success(request, "Has salido de tu zona de reservas.")
    return redirect("customers:client_access", slug=business.slug)


def _safe_next_url(request, business):
    fallback = reverse("public_booking", args=[business.slug])
    next_url = request.POST.get("next") or request.GET.get("next") or fallback
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback

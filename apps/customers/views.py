from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max, Min, Q
from django.forms import ValidationError as FormValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

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
from apps.core.features import transactional_email_delivery_enabled
from apps.customers.forms import (
    CUSTOMER_PRIVACY_UNAVAILABLE_QUICK_MESSAGE,
    ClientEmailVerificationForm,
    ClientInvitationActivationForm,
    ClientLoginForm,
    ClientPasswordResetForm,
    ClientPasswordResetRequestForm,
    ClientRegistrationForm,
    ProfessionalAuthorizedContactForm,
    ProfessionalClientEditForm,
    ProfessionalClientQuickForm,
)
from apps.legal.forms import CustomerPrivacyEvidenceForm
from apps.legal.models import LegalDocument
from apps.legal.presentations import (
    LEGAL_PRESENTATION_CHANGED_MESSAGE,
    LegalPresentationError,
    LegalPresentationScope,
    clear_legal_confirmation_fields,
    issue_legal_presentation,
    resolve_legal_presentation,
)
from apps.legal.services import (
    EVENT_FINGERPRINT_COLLISION_MESSAGE,
    business_can_collect_personal_data,
    business_legal_snapshot,
    customer_privacy_status,
    get_active_document,
    record_customer_privacy_information,
)
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessInvitation,
    BusinessClientAccessGrant,
    BusinessClientAuthorizedContact,
)
from apps.customers.services import (
    activate_claimed_invitation,
    create_client_access_invitation,
    dismiss_client_merge_candidate,
    find_available_invitation,
    get_client_merge_candidate,
    get_client_merge_candidates,
    get_claimed_invitation,
    get_session_client_access,
    lock_pending_public_registration_for_resend,
    login_client_access,
    logout_client_access,
    merge_client_records,
    public_registration_expiry,
    revoke_client_access_invitation,
    set_authorized_contact_active,
    set_client_access_active,
    set_professional_client_active,
    store_invitation_claim,
    toggle_contact_online_booking,
)
from apps.core.security_throttle import (
    THROTTLE_MESSAGE,
    ThrottleLimit,
    is_throttled,
    phone_throttle_key,
    record_failed_attempt,
    request_ip,
    reserve_throttle_attempts,
    settle_successful_throttle,
)
from apps.core.text import normalize_search_text
from apps.notifications.services import (
    client_password_reset_access_from_token,
    queue_client_email_verification,
    queue_client_password_reset,
    reset_client_password_from_token,
    unverified_client_from_token,
    verified_client_from_token,
)
from apps.notifications.models import OutboundEmail


CLIENT_EMAIL_PENDING_SESSION_KEY = "client_email_verification_pending"
CLIENT_GENERIC_EMAIL_MESSAGE = (
    "Si los datos corresponden a una cuenta disponible, recibirás un correo en unos minutos."
)
CLIENT_DEMO_EMAIL_MESSAGE = (
    "La solicitud se ha registrado, pero el envío de correos está desactivado "
    "en este entorno y no se ha enviado ningún enlace."
)
CUSTOMER_PRIVACY_UNAVAILABLE_EMAIL_MESSAGE = (
    "Ahora mismo no podemos mostrar la información de privacidad necesaria. "
    "No hemos creado la contraseña ni activado la cuenta. Inténtalo de nuevo más tarde."
)
CUSTOMER_PRIVACY_UNAVAILABLE_MANUAL_MESSAGE = (
    "No hay una política de privacidad vigente para registrar. "
    "No hemos guardado ninguna constancia. Inténtalo de nuevo más tarde."
)
CUSTOMER_PRIVACY_CONTROL_DISABLED_MESSAGE = (
    "El control de privacidad está desactivado para este negocio. "
    "No hemos guardado ninguna constancia."
)
_DOCUMENT_NOT_PROVIDED = object()


def _issue_customer_privacy_presentation(
    *,
    business,
    scope,
    audience,
    document=_DOCUMENT_NOT_PROVIDED,
):
    if not business.legal_compliance_enabled:
        return ""
    if document is _DOCUMENT_NOT_PROVIDED:
        document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
    if document is None:
        return ""
    return issue_legal_presentation(
        scope=scope,
        audience=audience,
        documents=(document,),
        legal_context=business_legal_snapshot(business),
    )


def _email_throttle_key(value):
    return str(value or "").strip().lower()


def _login_identifier_throttle_key(value):
    value = str(value or "").strip()
    if "@" in value:
        return _email_throttle_key(value)
    return phone_throttle_key(value)


def _queue_client_verification_if_allowed(
    request,
    access=None,
    *,
    business=None,
    email=None,
):
    business = access.business if access is not None else business
    normalized_email = access.email_normalized if access is not None else _email_throttle_key(email)
    cooldown_identity = access.pk if access is not None else f"pending:{normalized_email}"
    reservation = reserve_throttle_attempts(
        limits=(
            ThrottleLimit(
                "client_email_resend_cooldown",
                f"{business.pk}:{cooldown_identity}",
                1,
                60,
            ),
            ThrottleLimit(
                "client_email_resend_address",
                f"{business.pk}:{normalized_email}",
                5,
                60 * 60,
            ),
            ThrottleLimit(
                "client_email_resend_ip",
                request_ip(request),
                20,
                60 * 60,
            ),
        )
    )
    if not reservation.allowed or access is None:
        return None
    # Encolamos sin SMTP síncrono: la respuesta no delata por latencia si la
    # dirección corresponde a una cuenta y el timer entrega los correos debidos.
    if not access.is_pending_public_registration:
        return queue_client_email_verification(access)
    with transaction.atomic():
        access = lock_pending_public_registration_for_resend(access=access)
        if access is None:
            return None
        email = queue_client_email_verification(access)
        lease_is_active = bool(
            email.status == OutboundEmail.Status.PROCESSING
            and email.lease_expires_at is not None
            and email.lease_expires_at > timezone.now()
        )
        if not lease_is_active:
            access.public_registration_expires_at = public_registration_expiry()
            access.save(
                update_fields=["public_registration_expires_at", "updated_at"]
            )
        return email


@login_required
def professional_client_list(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    quick_form = ProfessionalClientQuickForm(business=business)
    validated_receipt = None
    response_status = 200
    if request.method == "POST":
        quick_form = ProfessionalClientQuickForm(request.POST, business=business)
        quick_form_is_valid = quick_form.is_valid()
        try:
            validated_receipt = quick_form.validate_legal_presentation(
                recorded_by=request.user,
                legal_presentation_token=request.POST.get(
                    "legal_presentation_token",
                    "",
                ),
            )
            if quick_form_is_valid:
                client, created = quick_form.save(
                    recorded_by=request.user,
                    legal_presentation_token=request.POST.get(
                        "legal_presentation_token",
                        "",
                    ),
                )
        except FormValidationError as exc:
            if CUSTOMER_PRIVACY_UNAVAILABLE_QUICK_MESSAGE in getattr(
                exc,
                "messages",
                (),
            ):
                response_status = 503
            if LEGAL_PRESENTATION_CHANGED_MESSAGE in getattr(exc, "messages", ()):
                validated_receipt = None
                clear_legal_confirmation_fields(
                    quick_form,
                    ("privacy_information_provided",),
                )
            if EVENT_FINGERPRINT_COLLISION_MESSAGE in getattr(exc, "messages", ()):
                validated_receipt = None
                clear_legal_confirmation_fields(
                    quick_form,
                    ("privacy_information_provided",),
                )
            quick_form.add_error(None, exc)
        else:
            if quick_form_is_valid:
                if created:
                    messages.success(request, f"Ficha creada para {client.full_name}.")
                else:
                    messages.success(request, f"{client.full_name} ya estaba en clientes.")
                return redirect("customers:professional_client_detail", client_id=client.id)
        business = quick_form.business

    search = (request.GET.get("q") or "").strip()
    status_filter = request.GET.get("status") or "active"
    if status_filter not in {"active", "inactive", "all"}:
        status_filter = "active"

    all_merge_candidates = get_client_merge_candidates(business=business)
    merge_candidates = all_merge_candidates
    if status_filter == "inactive":
        merge_candidates = ()
    elif search:
        normalized_search = normalize_search_text(search)
        phone_search = "".join(character for character in search if character.isdigit())
        merge_candidates = tuple(
            candidate
            for candidate in merge_candidates
            if normalized_search
            in candidate.professional_client.full_name_normalized
            or (
                phone_search
                and phone_search
                in candidate.professional_client.phone_normalized
            )
        )
    merge_candidate_client_ids = {
        client_id
        for candidate in all_merge_candidates
        for client_id in (
            candidate.professional_client.pk,
            candidate.online_client.pk,
        )
    }

    clients = (
        BusinessClient.objects.select_related("access")
        .filter(
            business=business,
            merged_into__isnull=True,
        )
        .exclude(pk__in=merge_candidate_client_ids)
    )
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
    clients = clients.annotate(
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
    ).order_by("full_name", "pk")
    clients_page = Paginator(clients, 6).get_page(request.GET.get("page"))

    selected_authorized_client = None
    selected_authorized_client_id = quick_form["authorized_business_client"].value()
    if selected_authorized_client_id:
        selected_authorized_client = (
            BusinessClient.objects.filter(
                business=business,
                is_active=True,
                merged_into__isnull=True,
                pk=selected_authorized_client_id,
            )
            .select_related("access")
            .first()
        )
    selected_authorized_access = (
        getattr(selected_authorized_client, "access", None)
        if selected_authorized_client is not None
        else None
    )
    if validated_receipt is not None:
        quick_privacy_document = validated_receipt.document(
            LegalDocument.Kind.CUSTOMER_PRIVACY
        )
        legal_presentation_token = request.POST.get("legal_presentation_token", "")
    else:
        quick_privacy_document = (
            get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
            if business.legal_compliance_enabled
            else None
        )
        legal_presentation_token = _issue_customer_privacy_presentation(
            business=business,
            scope=LegalPresentationScope.PROFESSIONAL_CLIENT_QUICK,
            audience={"business_id": business.pk, "user_id": request.user.pk},
            document=quick_privacy_document,
        )
    privacy_document_available = (
        not business.legal_compliance_enabled
        or quick_privacy_document is not None
    )
    if not privacy_document_available:
        if request.method == "POST" and CUSTOMER_PRIVACY_UNAVAILABLE_QUICK_MESSAGE not in tuple(
            str(error) for error in quick_form.non_field_errors()
        ):
            quick_form.add_error(None, CUSTOMER_PRIVACY_UNAVAILABLE_QUICK_MESSAGE)
        if request.method == "POST":
            response_status = 503

    return render(
        request,
        "professional/clients/list.html",
        {
            "business": business,
            "clients": clients_page,
            "clients_page": clients_page,
            "merge_candidates": merge_candidates,
            "search": search,
            "status_filter": status_filter,
            "quick_form": quick_form,
            "privacy_document": quick_privacy_document,
            "privacy_document_available": privacy_document_available,
            "customer_privacy_unavailable_message": (
                CUSTOMER_PRIVACY_UNAVAILABLE_QUICK_MESSAGE
            ),
            "legal_presentation_token": legal_presentation_token,
            "client_search_url": reverse("customers:professional_client_lookup"),
            "selected_authorized_client": selected_authorized_client,
            "selected_authorized_access": selected_authorized_access,
            "clients_count": (
                clients_page.paginator.count + len(merge_candidates)
            ),
            "active_clients_count": BusinessClient.objects.filter(
                business=business,
                is_active=True,
                merged_into__isnull=True,
            ).count()
            - len(all_merge_candidates),
            "inactive_clients_count": BusinessClient.objects.filter(
                business=business,
                is_active=False,
                merged_into__isnull=True,
            ).count(),
            "all_clients_count": (
                BusinessClient.objects.filter(
                    business=business,
                    merged_into__isnull=True,
                ).count()
                - len(all_merge_candidates)
            ),
        },
        status=response_status,
    )


@login_required
def professional_client_detail(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    business_client = _get_professional_client(
        business,
        client_id,
        include_merged=True,
    )
    if business_client.merged_into_id:
        messages.info(
            request,
            "Esta ficha ya se unificó. Te mostramos la ficha que reúne su información.",
        )
        return redirect(
            "customers:professional_client_detail",
            client_id=business_client.merged_into_id,
        )
    return render(
        request,
        "professional/clients/detail.html",
        _professional_client_context(business, business_client, request.user),
    )


@login_required
def professional_client_merge_review(
    request,
    professional_client_id,
    online_client_id,
):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    candidate = get_client_merge_candidate(
        business=business,
        professional_client_id=professional_client_id,
        online_client_id=online_client_id,
        include_dismissed=True,
    )
    if candidate is None:
        messages.info(
            request,
            "La coincidencia ha cambiado o ya no necesita revisión.",
        )
        return redirect("customers:professional_client_list")
    return render(
        request,
        "professional/clients/merge_review.html",
        {
            "business": business,
            "candidate": candidate,
            "professional_appointments_count": (
                candidate.professional_client.appointments.count()
            ),
            "online_appointments_count": (
                candidate.online_client.appointments.count()
            ),
            "appointments_total": (
                candidate.professional_client.appointments.count()
                + candidate.online_client.appointments.count()
            ),
        },
    )


@login_required
@require_POST
def professional_client_merge_confirm(
    request,
    professional_client_id,
    online_client_id,
):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    try:
        result = merge_client_records(
            business=business,
            professional_client_id=professional_client_id,
            online_client_id=online_client_id,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
        return redirect(
            "customers:professional_client_merge_review",
            professional_client_id=professional_client_id,
            online_client_id=online_client_id,
        )
    appointments_total = result.canonical_client.appointments.count()
    messages.success(
        request,
        (
            f"Fichas unificadas. {result.canonical_client.full_name} conserva "
            f"{appointments_total} cita"
            f"{'' if appointments_total == 1 else 's'} y su cuenta online."
        ),
    )
    return redirect(
        "customers:professional_client_detail",
        client_id=result.canonical_client.pk,
    )


@login_required
@require_POST
def professional_client_merge_dismiss(
    request,
    professional_client_id,
    online_client_id,
):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")
    try:
        dismiss_client_merge_candidate(
            business=business,
            professional_client_id=professional_client_id,
            online_client_id=online_client_id,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(
            request,
            "La coincidencia queda revisada. Las dos fichas se mantienen separadas.",
        )
    return redirect("customers:professional_client_list")


@login_required
@require_POST
def professional_client_privacy_record(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return redirect("accounts:no_business")

    business_client = _get_professional_client(business, client_id)
    form = CustomerPrivacyEvidenceForm(request.POST)
    form_is_valid = form.is_valid()
    privacy_unavailable = False
    try:
        with transaction.atomic():
            business = Business.objects.select_for_update().get(pk=business.pk)
            if not business.legal_compliance_enabled:
                privacy_unavailable = True
                raise ValidationError(CUSTOMER_PRIVACY_CONTROL_DISABLED_MESSAGE)
            privacy_document = (
                LegalDocument.objects.select_for_update()
                .filter(
                    kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
                    is_active=True,
                )
                .first()
            )
            if privacy_document is None:
                privacy_unavailable = True
                raise LegalPresentationError(
                    CUSTOMER_PRIVACY_UNAVAILABLE_MANUAL_MESSAGE
                )
            receipt = resolve_legal_presentation(
                request.POST.get("legal_presentation_token", ""),
                scope=LegalPresentationScope.PROFESSIONAL_CLIENT_PRIVACY,
                audience={
                    "business_id": business.pk,
                    "business_client_id": business_client.pk,
                    "user_id": request.user.pk,
                },
                required_kinds=(LegalDocument.Kind.CUSTOMER_PRIVACY,),
                legal_context=business_legal_snapshot(business),
            )
            if form_is_valid:
                record_customer_privacy_information(
                    business_client=business_client,
                    recorded_by=request.user,
                    channel=form.cleaned_data["channel"],
                    document=receipt.document(LegalDocument.Kind.CUSTOMER_PRIVACY),
                    legal_context_snapshot=receipt.legal_context,
                    action_fingerprint_source=receipt.receipt_id,
                )
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        if form_is_valid and not privacy_unavailable:
            messages.success(
                request,
                "Queda registrada la entrega de la información de privacidad vigente.",
            )
        else:
            messages.error(
                request,
                "Selecciona el canal utilizado para registrar la constancia.",
            )
    return redirect("customers:professional_client_detail", client_id=business_client.id)


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
            with transaction.atomic():
                business_client, access_to_verify = edit_form.save()
                if access_to_verify is not None:
                    if not transactional_email_delivery_enabled():
                        raise ValidationError(
                            "El correo de una cuenta online no puede cambiarse en esta "
                            "demostración porque no se entregan enlaces de verificación."
                        )
                    queue_client_email_verification(access_to_verify)
        except ValidationError as exc:
            edit_form.add_error(None, exc)
        else:
            if access_to_verify is not None:
                messages.success(
                    request,
                    f"Datos actualizados para {business_client.full_name}. "
                    "La cuenta deberá verificar el nuevo correo y crear una contraseña "
                    "antes de volver a entrar.",
                )
            else:
                messages.success(
                    request,
                    f"Datos actualizados para {business_client.full_name}.",
                )
            return redirect("customers:professional_client_detail", client_id=business_client.id)

    return render(
        request,
        "professional/clients/edit.html",
        {
            **_professional_client_context(business, business_client, request.user),
            "edit_form": edit_form,
            "transactional_email_enabled": transactional_email_delivery_enabled(),
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
            messages.success(
                request, f"La ficha de {business_client.full_name} vuelve a estar activa."
            )
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
@require_GET
def professional_client_search(request, client_id):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return JsonResponse({"results": []}, status=403)
    business_client = _get_professional_client(business, client_id)
    return _professional_client_search_response(
        request,
        business=business,
        excluded_client_id=business_client.id,
    )


@login_required
@require_GET
def professional_client_lookup(request):
    business = get_primary_business_for_user(request.user)
    if business is None:
        return JsonResponse({"results": []}, status=403)
    return _professional_client_search_response(request, business=business)


def _professional_client_search_response(request, *, business, excluded_client_id=None):
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    normalized_query = normalize_search_text(query)
    phone_digits = "".join(character for character in query if character.isdigit())
    filters = Q(full_name_normalized__contains=normalized_query)
    if phone_digits:
        filters |= Q(phone_normalized__contains=phone_digits)
    clients = BusinessClient.objects.filter(
        business=business,
        is_active=True,
        merged_into__isnull=True,
    )
    if excluded_client_id is not None:
        clients = clients.exclude(pk=excluded_client_id)
    clients = clients.filter(filters).select_related("access").order_by("full_name", "pk")[:8]
    results = []
    for client in clients:
        access = getattr(client, "access", None)
        results.append(
            {
                "id": client.id,
                "name": client.full_name,
                "phone": client.phone,
                "has_phone": bool(client.phone_normalized),
                "online_status": (
                    "active"
                    if access is not None and access.is_active
                    else "inactive"
                    if access is not None
                    else "none"
                ),
            }
        )
    response = JsonResponse({"results": results})
    response["Cache-Control"] = "no-store"
    return response


@login_required
@require_POST
def professional_contact_online_toggle(request, client_id, contact_id):
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
    try:
        grant = toggle_contact_online_booking(contact=contact)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        if grant.is_active:
            messages.success(
                request,
                f"{contact.full_name} ya puede reservar online para {business_client.full_name}.",
            )
        else:
            messages.success(
                request,
                f"{contact.full_name} ya no puede reservar online para esta ficha.",
            )
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


def _professional_client_context(business, business_client, actor_user):
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
        ).order_by("-starts_at", "-pk")[:12]
    )
    for appointment in history_appointments:
        appointment.operational_status_label = (
            "Pendiente de cierre"
            if appointment.is_pending_closure()
            else appointment.get_status_display()
        )

    client_access = getattr(business_client, "access", None)
    active_invitation = (
        business_client.access_invitations.filter(
            used_at__isnull=True,
            revoked_at__isnull=True,
            expires_at__gt=now,
        )
        .order_by("-created_at")
        .first()
    )
    authorized_contacts = list(
        business_client.authorized_contacts.select_related(
            "linked_business_client__access"
        ).order_by("-is_active", "-is_primary_contact", "full_name", "pk")
    )
    grant_by_contact = {
        grant.authorized_contact_id: grant
        for grant in BusinessClientAccessGrant.objects.filter(
            business_client=business_client,
            authorized_contact__in=authorized_contacts,
        )
    }
    for contact in authorized_contacts:
        contact.online_access = (
            getattr(contact.linked_business_client, "access", None)
            if contact.linked_business_client_id
            else None
        )
        contact.online_grant = grant_by_contact.get(contact.id)

    privacy_status = customer_privacy_status(business_client)
    privacy_document_available = (
        not business.legal_compliance_enabled
        or privacy_status["document"] is not None
    )
    return {
        "business": business,
        "business_client": business_client,
        "upcoming_appointments": upcoming_appointments,
        "history_appointments": history_appointments,
        "authorized_contacts": authorized_contacts,
        "active_contacts_count": business_client.authorized_contacts.filter(is_active=True).count(),
        "client_access": client_access,
        "active_invitation": active_invitation,
        "has_client_access": client_access is not None and client_access.is_active,
        "pending_confirmed_count": business_client.appointments.filter(
            status=Appointment.Status.CONFIRMED,
            ends_at__gt=now,
        ).count(),
        "privacy_status": privacy_status,
        "privacy_document_available": privacy_document_available,
        "customer_privacy_unavailable_message": (
            CUSTOMER_PRIVACY_UNAVAILABLE_MANUAL_MESSAGE
        ),
        "privacy_evidence_form": CustomerPrivacyEvidenceForm(),
        "legal_presentation_token": _issue_customer_privacy_presentation(
            business=business,
            scope=LegalPresentationScope.PROFESSIONAL_CLIENT_PRIVACY,
            audience={
                "business_id": business.pk,
                "business_client_id": business_client.pk,
                "user_id": actor_user.pk,
            },
            document=privacy_status["document"],
        ),
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

    selected_linked_client = None
    selected_linked_client_id = contact_form["linked_business_client"].value()
    if selected_linked_client_id:
        selected_linked_client = (
            BusinessClient.objects.filter(
                business=business,
                is_active=True,
                merged_into__isnull=True,
                pk=selected_linked_client_id,
            )
            .select_related("access")
            .first()
        )
    selected_access = (
        getattr(selected_linked_client, "access", None)
        if selected_linked_client is not None
        else None
    )

    return render(
        request,
        "professional/clients/contact_form.html",
        {
            **_professional_client_context(business, business_client, request.user),
            "contact_form": contact_form,
            "editing_contact": contact,
            "selected_linked_client": selected_linked_client,
            "selected_access": selected_access,
            "client_search_url": reverse(
                "customers:professional_client_search",
                args=[business_client.id],
            ),
        },
    )


def _get_professional_client(business, client_id, *, include_merged=False):
    clients = (
        BusinessClient.objects.select_related("access", "merged_into")
        .prefetch_related("authorized_contacts")
        .filter(business=business)
    )
    if not include_merged:
        clients = clients.filter(merged_into__isnull=True)
    return get_object_or_404(
        clients,
        id=client_id,
    )


def _validation_message(exc):
    messages_list = getattr(exc, "messages", None)
    if messages_list:
        return " ".join(messages_list)
    return str(exc)


def client_access(request, slug):
    # La pausa cierra nuevas reservas y altas, no la cuenta ni el ejercicio de
    # derechos de clientes existentes.
    business = get_object_or_404(Business, slug=slug)
    next_url = _safe_next_url(request, business)
    auth_theme = get_business_visual_theme(business)
    has_pending_booking = get_public_booking_draft(request, business) is not None
    account_only = not business.accepts_public_bookings()

    if get_session_client_access(request, business):
        return redirect(next_url)

    login_form = ClientLoginForm(business=business)
    response_status = 200

    if request.method == "POST":
        identifier = request.POST.get("identifier") or request.POST.get("phone", "")
        subject_key = f"{business.id}:{_login_identifier_throttle_key(identifier)}"
        ip_key = request_ip(request)
        reservation = reserve_throttle_attempts(
            limits=(
                ThrottleLimit("client_login_subject", subject_key, 5, 15 * 60),
                ThrottleLimit("client_login_ip", ip_key, 30, 15 * 60),
            )
        )
        if not reservation.allowed:
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
                settle_successful_throttle(
                    reservation,
                    reset_scopes={"client_login_subject"},
                )
                login_client_access(request, login_form.client_access)
                if account_only:
                    messages.success(
                        request,
                        "Has entrado en tu cuenta. Aunque las reservas estén pausadas, puedes consultar la privacidad y ejercer tus derechos.",
                    )
                else:
                    messages.success(request, "Has entrado en tu zona de reservas.")
                return redirect(next_url)
            if reservation.blocked_scopes:
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
            "account_only": account_only,
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

    if not business_can_collect_personal_data(business):
        return render(
            request,
            "legal/business_privacy_pending.html",
            {"business": business},
            status=503,
        )

    activation_form = ClientInvitationActivationForm(
        request.POST or None,
        business=business,
    )
    if request.method == "POST" and activation_form.is_valid():
        try:
            access, used_invitation = activate_claimed_invitation(
                request=request,
                business=business,
                email=activation_form.cleaned_data["email"],
            )
        except ValidationError:
            return _invitation_unavailable_response(request, business)
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
        _store_pending_email_verification(
            request,
            access,
            reverse("public_booking", args=[business.slug]),
        )
        _queue_client_verification_if_allowed(request, access)
        return redirect("customers:client_email_pending", slug=business.slug)

    response = render(
        request,
        "customers/client_invitation_activate.html",
        {
            "business": business,
            "business_client": invitation.business_client,
            "activation_form": activation_form,
            "transactional_email_enabled": transactional_email_delivery_enabled(),
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
        },
    )
    # La activación se confirma con un formulario POST en este mismo origen.
    response["Referrer-Policy"] = "same-origin"
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

    if not business_can_collect_personal_data(business):
        return render(
            request,
            "legal/business_privacy_pending.html",
            {"business": business},
            status=503,
        )

    if get_session_client_access(request, business):
        return redirect(next_url)

    registration_form = ClientRegistrationForm(business=business)
    response_status = 200

    if request.method == "POST":
        reservation = reserve_throttle_attempts(
            limits=(
                ThrottleLimit(
                    "client_registration_email",
                    f"{business.pk}:{_email_throttle_key(request.POST.get('email'))}",
                    3,
                    60 * 60,
                ),
                ThrottleLimit(
                    "client_registration_phone",
                    f"{business.pk}:{phone_throttle_key(request.POST.get('phone'))}",
                    3,
                    60 * 60,
                ),
                ThrottleLimit(
                    "client_registration_ip",
                    request_ip(request),
                    20,
                    60 * 60,
                ),
            )
        )
        registration_form = ClientRegistrationForm(
            request.POST,
            business=business,
        )
        if not reservation.allowed:
            registration_form.is_valid()
            registration_form.add_error(None, THROTTLE_MESSAGE)
            response_status = 429
        elif registration_form.is_valid():
            try:
                access = registration_form.save()
            except FormValidationError:
                # La respuesta pública no distingue una dirección ya usada de
                # un alta recién creada. Tampoco se enlaza ni modifica la cuenta previa.
                session_access = None
                _store_pending_email_verification(
                    request,
                    session_access,
                    next_url,
                    business=business,
                    email=registration_form.cleaned_data["email"],
                )
                _queue_client_verification_if_allowed(
                    request,
                    session_access,
                    business=business,
                    email=registration_form.cleaned_data["email"],
                )
                return redirect("customers:client_email_pending", slug=business.slug)
            else:
                _store_pending_email_verification(
                    request,
                    access,
                    next_url,
                )
                _queue_client_verification_if_allowed(request, access)
                return redirect("customers:client_email_pending", slug=business.slug)

    response = render(
        request,
        "customers/client_register.html",
        {
            "business": business,
            "registration_form": registration_form,
            "next_url": next_url,
            "client_auth_theme": auth_theme,
            "client_auth_image_url": get_business_public_image_url(business),
            "has_pending_booking": has_pending_booking,
            "transactional_email_enabled": transactional_email_delivery_enabled(),
        },
        status=response_status,
    )
    response["Cache-Control"] = "no-store"
    return response


def _store_pending_email_verification(
    request,
    access,
    next_url,
    *,
    business=None,
    email=None,
):
    business = access.business if access is not None else business
    request.session[CLIENT_EMAIL_PENDING_SESSION_KEY] = {
        "access_id": access.pk if access is not None else None,
        "business_id": business.pk,
        "email": access.email if access is not None else _email_throttle_key(email),
        "next": next_url,
    }


def client_email_pending(request, slug):
    business = get_object_or_404(Business, slug=slug)
    pending = request.session.get(CLIENT_EMAIL_PENDING_SESSION_KEY, {})
    if pending.get("business_id") != business.pk or not pending.get("email"):
        return redirect("customers:client_access", slug=business.slug)
    access = (
        BusinessClientAccess.objects.select_related("business_client")
        .filter(
            pk=pending.get("access_id"),
            business=business,
            is_active=True,
            email_verified_at__isnull=True,
        )
        .first()
    )
    if request.method == "POST":
        _queue_client_verification_if_allowed(
            request,
            access,
            business=business,
            email=pending["email"],
        )
        messages.info(
            request,
            (
                CLIENT_GENERIC_EMAIL_MESSAGE
                if transactional_email_delivery_enabled()
                else CLIENT_DEMO_EMAIL_MESSAGE
            ),
        )
        return redirect("customers:client_email_pending", slug=business.slug)
    response = render(
        request,
        "customers/client_email_pending.html",
        {
            "business": business,
            "pending_email": pending["email"],
            "account_only": not business.accepts_public_bookings(),
            "transactional_email_enabled": transactional_email_delivery_enabled(),
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
        },
    )
    response["Cache-Control"] = "no-store"
    return response


def client_email_verify(request, slug, token):
    # Un negocio puede pausarse entre el envío del correo y la apertura del
    # enlace. La pausa impide nuevas reservas, pero no debe dejar una identidad
    # a medio verificar ni inutilizar un enlace ya emitido.
    business = get_object_or_404(Business, slug=slug)
    access = unverified_client_from_token(token, business=business)
    if access is None:
        response = render(
            request,
            "customers/client_email_verified.html",
            {
                "business": business,
                "verification_valid": False,
                "client_auth_theme": get_business_visual_theme(business),
                "client_auth_image_url": get_business_public_image_url(business),
            },
            status=410,
        )
        response["Referrer-Policy"] = "no-referrer"
        response["Cache-Control"] = "no-store"
        return response

    verification_form = ClientEmailVerificationForm(
        request.POST or None,
        business=business,
        access=access,
    )
    verification_form_is_valid = (
        verification_form.is_valid() if request.method == "POST" else False
    )
    privacy_receipt = None
    locked_business = business
    if request.method == "POST":
        try:
            with transaction.atomic():
                locked_business = Business.objects.select_for_update().get(pk=business.pk)
                if locked_business.legal_compliance_enabled:
                    locked_privacy_document = (
                        LegalDocument.objects.select_for_update()
                        .filter(
                            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
                            is_active=True,
                        )
                        .first()
                    )
                    if locked_privacy_document is None:
                        raise ValidationError(
                            CUSTOMER_PRIVACY_UNAVAILABLE_EMAIL_MESSAGE
                        )
                    privacy_receipt = resolve_legal_presentation(
                        request.POST.get("legal_presentation_token", ""),
                        scope=LegalPresentationScope.CLIENT_EMAIL_VERIFICATION,
                        audience={
                            "business_id": locked_business.pk,
                            "client_access_id": access.pk,
                        },
                        required_kinds=(LegalDocument.Kind.CUSTOMER_PRIVACY,),
                        legal_context=business_legal_snapshot(locked_business),
                    )
                if verification_form_is_valid:
                    access = verified_client_from_token(
                        token,
                        business=locked_business,
                        password=verification_form.cleaned_data["password"],
                        full_name=verification_form.cleaned_data.get("full_name"),
                        phone=verification_form.cleaned_data.get("phone"),
                        privacy_acknowledged=verification_form.cleaned_data.get(
                            "privacy_acknowledged",
                            False,
                        ),
                        privacy_document=(
                            privacy_receipt.document(
                                LegalDocument.Kind.CUSTOMER_PRIVACY
                            )
                            if privacy_receipt is not None
                            else None
                        ),
                        privacy_legal_context=(
                            privacy_receipt.legal_context
                            if privacy_receipt is not None
                            else None
                        ),
                        privacy_action_fingerprint_source=(
                            privacy_receipt.receipt_id
                            if privacy_receipt is not None
                            else None
                        ),
                    )
        except ValidationError as exc:
            if CUSTOMER_PRIVACY_UNAVAILABLE_EMAIL_MESSAGE in getattr(
                exc,
                "messages",
                (),
            ):
                privacy_receipt = None
                clear_legal_confirmation_fields(
                    verification_form,
                    ("privacy_acknowledged",),
                )
            if LEGAL_PRESENTATION_CHANGED_MESSAGE in getattr(exc, "messages", ()):
                privacy_receipt = None
                clear_legal_confirmation_fields(
                    verification_form,
                    ("privacy_acknowledged",),
                )
            if EVENT_FINGERPRINT_COLLISION_MESSAGE in getattr(exc, "messages", ()):
                privacy_receipt = None
                clear_legal_confirmation_fields(
                    verification_form,
                    ("privacy_acknowledged",),
                )
            verification_form.add_error(None, exc)
        else:
            business = locked_business
            if verification_form_is_valid:
                if access is None:
                    response = render(
                        request,
                        "customers/client_email_verified.html",
                        {
                            "business": business,
                            "verification_valid": False,
                            "client_auth_theme": get_business_visual_theme(business),
                            "client_auth_image_url": get_business_public_image_url(business),
                        },
                        status=410,
                    )
                    response["Referrer-Policy"] = "no-referrer"
                    response["Cache-Control"] = "no-store"
                    return response
                login_client_access(request, access)
                pending = request.session.pop(CLIENT_EMAIL_PENDING_SESSION_KEY, {})
                next_url = pending.get("next") if pending.get("business_id") == business.pk else ""
                business.refresh_from_db(fields=["is_active", "public_booking_enabled"])
                if business.accepts_public_bookings():
                    messages.success(
                        request,
                        "Correo confirmado y contraseña creada. Ya puedes continuar con tu reserva.",
                    )
                    destination = next_url or reverse("public_booking", args=[business.slug])
                else:
                    messages.success(
                        request,
                        "Correo confirmado y contraseña creada. El negocio está pausado ahora mismo, pero tu cuenta ya queda preparada.",
                    )
                    destination = reverse("legal:business_privacy", args=[business.slug])
                response = redirect(destination)
                response["Referrer-Policy"] = "no-referrer"
                response["Cache-Control"] = "no-store"
                return response
        business = locked_business

    if privacy_receipt is not None:
        privacy_document = privacy_receipt.document(
            LegalDocument.Kind.CUSTOMER_PRIVACY
        )
        legal_presentation_token = request.POST.get("legal_presentation_token", "")
    else:
        privacy_document = (
            get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
            if business.legal_compliance_enabled
            else None
        )
        legal_presentation_token = _issue_customer_privacy_presentation(
            business=business,
            scope=LegalPresentationScope.CLIENT_EMAIL_VERIFICATION,
            audience={"business_id": business.pk, "client_access_id": access.pk},
            document=privacy_document,
        )
    privacy_document_available = (
        not business.legal_compliance_enabled or privacy_document is not None
    )
    if not privacy_document_available:
        if request.method == "POST" and CUSTOMER_PRIVACY_UNAVAILABLE_EMAIL_MESSAGE not in tuple(
            str(error) for error in verification_form.non_field_errors()
        ):
            verification_form.add_error(
                None,
                CUSTOMER_PRIVACY_UNAVAILABLE_EMAIL_MESSAGE,
            )
        legal_presentation_token = ""
    response = render(
        request,
        "customers/client_email_verified.html",
        {
            "business": business,
            "access": access,
            "verification_form": verification_form,
            "verification_valid": True,
            "privacy_document": privacy_document,
            "privacy_document_available": privacy_document_available,
            "legal_presentation_token": legal_presentation_token,
            "customer_privacy_unavailable_message": (
                CUSTOMER_PRIVACY_UNAVAILABLE_EMAIL_MESSAGE
            ),
            "customer_privacy_unavailable_class": "login-form-error",
            "can_correct_public_profile": (
                verification_form.can_correct_public_profile
            ),
            "registration_activation_paused": (
                access.is_pending_public_registration and not business.accepts_public_bookings()
            ),
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
        },
        status=200 if privacy_document_available else 503,
    )
    # El token está en la URL. Conservamos un Origin válido para el POST CSRF,
    # pero nunca enviamos la ruta completa como Referer ni siquiera al mismo sitio.
    response["Referrer-Policy"] = "strict-origin"
    response["Cache-Control"] = "no-store"
    return response


def client_password_reset_request(request, slug):
    business = get_object_or_404(Business, slug=slug)
    reset_form = ClientPasswordResetRequestForm(request.POST or None)
    submitted = False
    if request.method == "POST":
        reservation = reserve_throttle_attempts(
            limits=(
                ThrottleLimit(
                    "client_password_reset_email",
                    f"{business.pk}:{_email_throttle_key(request.POST.get('email'))}",
                    3,
                    60 * 60,
                ),
                ThrottleLimit(
                    "client_password_reset_ip",
                    request_ip(request),
                    20,
                    60 * 60,
                ),
            )
        )
        if reset_form.is_valid():
            submitted = True
            if reservation.allowed:
                access = (
                    BusinessClientAccess.objects.select_related("business", "business_client")
                    .filter(
                        business=business,
                        email_normalized=reset_form.cleaned_data["email"].lower(),
                        email_verified_at__isnull=False,
                        is_active=True,
                        business_client__is_active=True,
                    )
                    .first()
                )
                if access is not None:
                    # La respuesta es idéntica para cuentas existentes y ausentes;
                    # el envío real queda en la cola para no filtrar existencia por latencia.
                    queue_client_password_reset(access)

    response = render(
        request,
        "customers/client_password_reset_request.html",
        {
            "business": business,
            "reset_form": reset_form,
            "submitted": submitted,
            "generic_message": (
                CLIENT_GENERIC_EMAIL_MESSAGE
                if transactional_email_delivery_enabled()
                else CLIENT_DEMO_EMAIL_MESSAGE
            ),
            "account_only": not business.accepts_public_bookings(),
            "transactional_email_enabled": transactional_email_delivery_enabled(),
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
        },
    )
    response["Cache-Control"] = "no-store"
    return response


def client_password_reset(request, slug, token):
    business = get_object_or_404(Business, slug=slug)
    access = client_password_reset_access_from_token(token, business=business)
    if access is None:
        response = render(
            request,
            "customers/client_password_reset.html",
            {
                "business": business,
                "reset_valid": False,
                "client_auth_theme": get_business_visual_theme(business),
                "client_auth_image_url": get_business_public_image_url(business),
            },
            status=410,
        )
        response["Referrer-Policy"] = "no-referrer"
        response["Cache-Control"] = "no-store"
        return response

    reset_form = ClientPasswordResetForm(request.POST or None)
    if request.method == "POST" and reset_form.is_valid():
        access = reset_client_password_from_token(
            token,
            business=business,
            password=reset_form.cleaned_data["password"],
        )
        if access is None:
            response = render(
                request,
                "customers/client_password_reset.html",
                {
                    "business": business,
                    "reset_valid": False,
                    "client_auth_theme": get_business_visual_theme(business),
                    "client_auth_image_url": get_business_public_image_url(business),
                },
                status=410,
            )
            response["Referrer-Policy"] = "no-referrer"
            response["Cache-Control"] = "no-store"
            return response
        business.refresh_from_db(fields=["is_active", "public_booking_enabled"])
        if business.accepts_public_bookings():
            logout_client_access(request)
            messages.success(request, "Contraseña actualizada. Ya puedes entrar con la nueva.")
            response = redirect("customers:client_access", slug=business.slug)
        else:
            # El enlace ya acredita el control del correo. Dejamos la nueva
            # sesión vinculada a la contraseña recién elegida para que la
            # privacidad no provoque un bucle de vuelta al login.
            login_client_access(request, access)
            messages.success(
                request,
                "Contraseña actualizada. Ya puedes acceder a tu cuenta y a la información de privacidad.",
            )
            response = redirect("legal:business_privacy", slug=business.slug)
        response["Referrer-Policy"] = "no-referrer"
        response["Cache-Control"] = "no-store"
        return response

    response = render(
        request,
        "customers/client_password_reset.html",
        {
            "business": business,
            "access": access,
            "reset_form": reset_form,
            "reset_valid": True,
            "client_auth_theme": get_business_visual_theme(business),
            "client_auth_image_url": get_business_public_image_url(business),
        },
    )
    # El token está en la URL. Conservamos un Origin válido para el POST CSRF,
    # pero nunca enviamos la ruta completa como Referer ni siquiera al mismo sitio.
    response["Referrer-Policy"] = "strict-origin"
    response["Cache-Control"] = "no-store"
    return response


@require_POST
def client_logout(request, slug):
    business = get_object_or_404(Business, slug=slug)
    logout_client_access(request)
    messages.success(request, "Has salido de tu cuenta de cliente.")
    return redirect("customers:client_access", slug=business.slug)


def _safe_next_url(request, business):
    if not business.accepts_public_bookings():
        return reverse("legal:business_privacy", args=[business.slug])
    fallback = reverse("public_booking", args=[business.slug])
    next_url = request.POST.get("next") or request.GET.get("next") or fallback
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback

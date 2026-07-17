from __future__ import annotations

import uuid
from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.booking.models import (
    Appointment,
    AppointmentService,
    AvailabilityRule,
    BusinessCalendarSettings,
    BusinessClosure,
    Service,
    WorkLine,
)
from apps.businesses.activity import record_business_activity
from apps.businesses.models import (
    Business,
    BusinessActivityEvent,
    BusinessMembership,
    PlatformSettings,
)
from apps.core.demo_scenario import (
    ACCESSES,
    APPOINTMENTS,
    BUSINESS_MARI,
    BUSINESS_NORTE,
    CLIENTS,
    DEMO_PASSWORD,
    RELATIONSHIPS,
    SERVICES,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_NO_SHOW,
    appointment_duration_minutes,
    future_business_days,
    previous_business_days,
    resolve_day_token,
    validate_scenario,
)
from apps.core.phone import normalize_phone
from apps.core.text import normalize_search_text
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessGrant,
    BusinessClientAuthorizedContact,
)
from apps.holidays.models import HolidaySyncRun, OfficialHoliday
from apps.legal.models import (
    BusinessLegalProfile,
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)
from apps.legal.services import (
    accept_professional_legal_documents,
    acknowledge_customer_privacy,
    business_legal_snapshot,
    get_active_document,
    record_customer_privacy_information,
)
from apps.notifications.models import InternalNotification


MADRID = ZoneInfo("Europe/Madrid")
DEMO_UUID_NAMESPACE = uuid.UUID("942679fe-471f-4bdb-b09e-ec76820403c5")

BUSINESS_DEFINITIONS = {
    BUSINESS_MARI: {
        "slug": "peluqueria-mari",
        "commercial_name": "Peluquería Mari",
        "public_description": (
            "Peluquería y salón de belleza para corte, color, tratamientos y peinados."
        ),
        "public_phone": "+34 600 111 001",
        "public_email": "hola@peluqueriamari.local",
        "address": "Calle Mayor 12",
        "city": "Madrid",
        "province": "Madrid",
        "public_image_preset": Business.PublicImagePreset.SALON,
        "line_count": 3,
        "line_name": "Línea {number}",
        "opening_time": time(9, 0),
        "weekly_rules": {
            0: ((time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))),
            1: ((time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))),
            2: ((time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))),
            3: ((time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))),
            4: ((time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))),
            5: ((time(9, 0), time(14, 0)),),
        },
    },
    BUSINESS_NORTE: {
        "slug": "barberia-norte",
        "commercial_name": "Barbería Norte",
        "public_description": (
            "Barbería de corte masculino, afeitado y cuidado personal con reserva online."
        ),
        "public_phone": "+34 600 222 001",
        "public_email": "hola@barberianorte.local",
        "address": "Avenida Norte 18",
        "city": "Madrid",
        "province": "Madrid",
        "public_image_preset": Business.PublicImagePreset.BARBERSHOP,
        "line_count": 2,
        "line_name": "Silla {number}",
        "opening_time": time(10, 0),
        "weekly_rules": {
            0: ((time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))),
            1: ((time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))),
            2: ((time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))),
            3: ((time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))),
            4: ((time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))),
            5: ((time(10, 0), time(14, 0)),),
        },
    },
}

LEGACY_SERVICE_ALIASES = {
    BUSINESS_MARI: {
        "wash": ("Lavado",),
        "cut": ("Corte",),
        "dry": ("Peinado",),
        "full_color": ("Tinte",),
        "hydration": ("Tratamiento hidratante",),
        "perm": ("Moldeador clásico", "Moldeador clasico"),
    },
    BUSINESS_NORTE: {
        "classic": ("Corte caballero",),
        "fade": ("Degradado",),
        "beard": ("Arreglo de barba",),
    },
}


class Command(BaseCommand):
    help = "Crea o actualiza el escenario académico reproducible de AgendaSalon."

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-date",
            default="",
            help=(
                "Fecha de referencia de la demo en formato YYYY-MM-DD. "
                "Si se omite, se utiliza la fecha actual de Madrid."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            anchor_date = (
                date.fromisoformat(options["base_date"])
                if options["base_date"]
                else timezone.localdate()
            )
        except ValueError as exc:
            raise CommandError("--base-date debe usar formato YYYY-MM-DD.") from exc

        _acquire_demo_seed_lock()
        current_now = timezone.now().astimezone(MADRID)
        reference_now = (
            min(
                current_now,
                datetime.combine(anchor_date, time(2, 5), tzinfo=MADRID),
            )
            if options["base_date"]
            else current_now
        )
        demo = DemoSeeder(anchor_date=anchor_date, reference_now=reference_now)
        summary = demo.run()

        self.stdout.write(
            self.style.SUCCESS("Escenario académico de AgendaSalon creado o actualizado.")
        )
        self.stdout.write(f"Fecha de referencia: {anchor_date.isoformat()}")
        self.stdout.write(f"Superadministración: {summary['superadmin_phone']}")
        self.stdout.write(f"Profesional Peluquería Mari: {summary['professional_phone']}")
        self.stdout.write(f"Profesional Barbería Norte: {summary['secondary_professional_phone']}")
        self.stdout.write(
            f"Datos canónicos: {summary['clients']} clientes, "
            f"{summary['services']} servicios y {summary['appointments']} citas."
        )
        self.stdout.write(f"Día sin hueco continuo de 180 minutos: {summary['no_capacity_date']}")


class DemoSeeder:
    def __init__(self, *, anchor_date: date, reference_now: datetime | None = None):
        self.anchor_date = anchor_date
        self.reference_now = reference_now or timezone.now()
        if timezone.is_naive(self.reference_now):
            raise ValueError("reference_now debe incluir zona horaria.")
        self.reference_now = self.reference_now.astimezone(MADRID)

        history_anchor = min(anchor_date, self.reference_now.date())
        excluded_holidays = set(
            OfficialHoliday.objects.filter(
                scope=OfficialHoliday.Scope.NATIONAL,
                date__gte=history_anchor - timedelta(days=60),
                date__lte=max(anchor_date, self.reference_now.date()) + timedelta(days=40),
            )
            .exclude(official_reference="PFM-LOCAL")
            .values_list("date", flat=True)
        )
        self.past_days = previous_business_days(
            history_anchor,
            20,
            excluded_dates=excluded_holidays,
        )
        self.future_days = future_business_days(
            anchor_date,
            self.reference_now,
            13,
            first_opening_time=time(9, 0),
            excluded_dates=excluded_holidays,
        )
        self.no_capacity_date = self.future_days[2]
        anchor_start = datetime.combine(anchor_date, time(2, 5), tzinfo=MADRID)
        self.activity_anchor = min(self.reference_now, anchor_start)
        earliest_scenario_day = self.past_days[-1]
        self.record_origin_at = _at(
            earliest_scenario_day - timedelta(days=21),
            time(9, 0),
        )
        self.legal_anchor = _at(
            earliest_scenario_day - timedelta(days=14),
            time(10, 0),
        )

    def run(self):
        validate_scenario()
        self._create_users()
        self._create_businesses()
        self._create_memberships()
        self._reset_demo_operational_data()
        self._create_calendars()
        self._sync_services()
        self._sync_work_lines()
        self._sync_clients()
        self._sync_client_accesses()
        self._sync_representatives()
        self._create_closures()
        appointments = self._create_appointments()
        self._create_notifications(appointments)
        self._create_legal_demo()
        self._create_activity_events(appointments)
        self._update_client_activity()
        self._validate_postflight()

        return {
            "superadmin_phone": self.superadmin.normalized_phone,
            "professional_phone": self.professionals[BUSINESS_MARI].normalized_phone,
            "secondary_professional_phone": self.professionals[BUSINESS_NORTE].normalized_phone,
            "clients": BusinessClient.objects.filter(business__in=self.businesses.values()).count(),
            "services": Service.objects.filter(business__in=self.businesses.values()).count(),
            "appointments": Appointment.objects.filter(
                business__in=self.businesses.values()
            ).count(),
            "no_capacity_date": self.no_capacity_date.isoformat(),
        }

    def _create_users(self):
        self.superadmin = self._upsert_user(
            phone="+34910000001",
            full_name="Admin AgendaSalon",
            email="admin@agendasalon.local",
            is_staff=True,
            is_superuser=True,
        )
        PlatformSettings.objects.get_or_create(
            pk=PlatformSettings.SINGLETON_PK,
            defaults={"updated_by": self.superadmin},
        )
        self.professionals = {
            BUSINESS_MARI: self._upsert_user(
                phone="+34600111001",
                full_name="Mari Profesional",
                email="mari@agendasalon.local",
                is_staff=False,
                is_superuser=False,
            ),
            BUSINESS_NORTE: self._upsert_user(
                phone="+34600222001",
                full_name="Norte Profesional",
                email="equipo@barberianorte.local",
                is_staff=False,
                is_superuser=False,
            ),
        }

    def _upsert_user(self, *, phone, full_name, email, is_staff, is_superuser):
        User = get_user_model()
        normalized_phone = normalize_phone(phone)
        user = User.objects.filter(normalized_phone=normalized_phone).first()
        if user is None:
            user = User(normalized_phone=normalized_phone, phone=phone)
        if not user.check_password(DEMO_PASSWORD):
            user.set_password(DEMO_PASSWORD)
        user.full_name = full_name
        user.email = email
        user.email_verified_at = self.legal_anchor
        user.email_verification_required = False
        user.phone = phone
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.is_active = True
        user.password_change_required = False
        user.last_login = None
        user.date_joined = self.record_origin_at
        user.full_clean()
        user.save()
        return user

    def _create_businesses(self):
        self.businesses = {}
        for business_key, definition in BUSINESS_DEFINITIONS.items():
            business = _update_first_or_create(
                Business,
                {"slug": definition["slug"]},
                {
                    "commercial_name": definition["commercial_name"],
                    "public_description": definition["public_description"],
                    "public_phone": definition["public_phone"],
                    "public_email": definition["public_email"],
                    "address": definition["address"],
                    "city": definition["city"],
                    "province": definition["province"],
                    "public_booking_enabled": True,
                    "public_image_preset": definition["public_image_preset"],
                    "is_active": True,
                },
            )
            _set_created_at(business, self.record_origin_at)
            self.businesses[business_key] = business

    def _create_memberships(self):
        for business_key, business in self.businesses.items():
            membership = _update_first_or_create(
                BusinessMembership,
                {"business": business, "user": self.professionals[business_key]},
                {
                    "role": BusinessMembership.Role.PROFESSIONAL_ADMIN,
                    "is_active": True,
                },
            )
            _set_created_at(membership, self.record_origin_at + timedelta(hours=1))

    def _reset_demo_operational_data(self):
        businesses = tuple(self.businesses.values())
        InternalNotification.objects.filter(business__in=businesses).delete()
        BusinessActivityEvent.objects.filter(business__in=businesses).delete()
        Appointment.objects.filter(business__in=businesses).delete()
        BusinessClosure.objects.filter(business__in=businesses).delete()
        AvailabilityRule.objects.filter(business__in=businesses).delete()

        # En el entorno académico estas constancias pertenecen a identidades
        # ficticias. Se reconstruyen de forma expresa para mantener una cronología
        # coherente; la operativa normal del producto continúa siendo append-only.
        CustomerPrivacyEvidence.objects.filter(business__in=businesses).delete()
        LegalAcceptance.objects.filter(business__in=businesses).delete()
        CustomerPrivacyEvidenceEvent._base_manager.filter(business__in=businesses).delete()
        LegalAcceptanceEvent._base_manager.filter(business__in=businesses).delete()

        # Las antiguas versiones del seed añadían un festivo nacional ficticio.
        # Se retira solo esa fuente local; los registros reales del BOE se conservan.
        OfficialHoliday.objects.filter(official_reference="PFM-LOCAL").delete()
        HolidaySyncRun.objects.filter(
            source_name__in=("Calendario local AgendaSalon", "Datos demo AgendaSalon")
        ).delete()

    def _create_calendars(self):
        for business_key, business in self.businesses.items():
            settings = _update_first_or_create(
                BusinessCalendarSettings,
                {"business": business},
                {
                    "slot_interval_minutes": 15,
                    "apply_national_holidays": True,
                },
            )
            settings.full_clean()
            settings.save()
            for weekday, intervals in BUSINESS_DEFINITIONS[business_key]["weekly_rules"].items():
                for start_time, end_time in intervals:
                    rule = AvailabilityRule(
                        business=business,
                        weekday=weekday,
                        start_time=start_time,
                        end_time=end_time,
                        is_active=True,
                    )
                    rule.full_clean()
                    rule.save()

    def _sync_services(self):
        self.services = {BUSINESS_MARI: {}, BUSINESS_NORTE: {}}
        for business_key, business in self.businesses.items():
            keep_ids = []
            used_ids = set()
            existing_services = list(Service.objects.filter(business=business).order_by("pk"))
            definitions = [item for item in SERVICES if item.business == business_key]
            aliases_by_key = LEGACY_SERVICE_ALIASES.get(business_key, {})

            for definition in definitions:
                accepted_names = (definition.name, *aliases_by_key.get(definition.key, ()))
                accepted_normalized = {normalize_search_text(name) for name in accepted_names}
                candidates = [
                    service
                    for service in existing_services
                    if service.pk not in used_ids
                    and normalize_search_text(service.name) in accepted_normalized
                ]
                service = next(
                    (candidate for candidate in candidates if candidate.name == definition.name),
                    candidates[0] if candidates else Service(business=business),
                )
                service.business = business
                service.name = definition.name
                service.description = definition.description
                service.duration_minutes = definition.duration_minutes
                service.price_amount = definition.price_amount
                service.color_hex = definition.color_hex
                service.is_active = definition.is_active
                service.display_order = definition.display_order
                service.full_clean()
                service.save()
                _set_created_at(
                    service,
                    self.record_origin_at + timedelta(days=1),
                )
                keep_ids.append(service.pk)
                used_ids.add(service.pk)
                self.services[business_key][definition.key] = service

            Service.objects.filter(business=business).exclude(pk__in=keep_ids).delete()

    def _sync_work_lines(self):
        self.lines = {BUSINESS_MARI: {}, BUSINESS_NORTE: {}}
        for business_key, business in self.businesses.items():
            definition = BUSINESS_DEFINITIONS[business_key]
            keep_ids = []
            for number in range(1, definition["line_count"] + 1):
                line = _update_first_or_create(
                    WorkLine,
                    {"business": business, "line_number": number},
                    {
                        "name": definition["line_name"].format(number=number),
                        "is_active": True,
                        "display_order": number,
                    },
                )
                line.full_clean()
                line.save()
                keep_ids.append(line.pk)
                self.lines[business_key][number] = line
            WorkLine.objects.filter(business=business).exclude(pk__in=keep_ids).delete()

    def _sync_clients(self):
        self.clients = {BUSINESS_MARI: {}, BUSINESS_NORTE: {}}
        for definition in CLIENTS:
            business = self.businesses[definition.business]
            queryset = BusinessClient.objects.filter(business=business)
            if definition.phone:
                queryset = queryset.filter(phone_normalized=normalize_phone(definition.phone))
            else:
                queryset = queryset.filter(
                    full_name_normalized=normalize_search_text(definition.full_name),
                    phone_normalized="",
                )
            client = queryset.first() or BusinessClient(business=business)
            client.business = business
            client.full_name = definition.full_name
            client.phone = definition.phone
            client.email = ""
            client.source = BusinessClient.Source.IMPORTED_DEMO
            client.is_active = definition.is_active
            client.internal_notes = definition.internal_notes
            client.last_activity_at = None
            client.full_clean()
            client.save()
            _set_created_at(
                client,
                self.record_origin_at + timedelta(days=2),
            )
            self.clients[definition.business][definition.key] = client

    def _sync_client_accesses(self):
        self.accesses = {BUSINESS_MARI: {}, BUSINESS_NORTE: {}}
        for definition in ACCESSES:
            business = self.businesses[definition.business]
            client = self.clients[definition.business][definition.client_key]
            access = BusinessClientAccess.objects.filter(
                business=business,
                business_client=client,
            ).first()
            if access is None:
                access = BusinessClientAccess(
                    business=business,
                    business_client=client,
                )
            access.business = business
            access.business_client = client
            access.phone = client.phone
            access.email = definition.email
            access.email_verified_at = self.activity_anchor if definition.email_verified else None
            access.is_active = definition.is_active
            access.is_pending_public_registration = False
            access.public_registration_expires_at = None
            access.last_login_at = None
            if not access.check_password(definition.password):
                access.set_password(definition.password)
            access.full_clean()
            access.save()
            _set_created_at(
                access,
                self.record_origin_at + timedelta(days=3),
            )
            client.email = definition.email
            client.save(update_fields=["email", "updated_at"])
            self_grant, _ = BusinessClientAccessGrant.objects.update_or_create(
                access=access,
                business_client=client,
                defaults={
                    "business": business,
                    "authorized_contact": None,
                    "relationship_label": BusinessClientAccessGrant.Relationship.SELF,
                    "is_active": True,
                },
            )
            _set_created_at(
                self_grant,
                self.record_origin_at + timedelta(days=3),
            )
            self.accesses[definition.business][definition.client_key] = access

    def _sync_representatives(self):
        businesses = tuple(self.businesses.values())
        BusinessClientAccessGrant.objects.filter(business__in=businesses).exclude(
            relationship_label=BusinessClientAccessGrant.Relationship.SELF
        ).delete()
        BusinessClientAuthorizedContact.objects.filter(business__in=businesses).delete()

        for definition in RELATIONSHIPS:
            business = self.businesses[definition.business]
            representative = self.clients[definition.business][definition.representative_key]
            beneficiary = self.clients[definition.business][definition.beneficiary_key]
            contact = BusinessClientAuthorizedContact(
                business=business,
                business_client=beneficiary,
                linked_business_client=representative,
                full_name=representative.full_name,
                phone=representative.phone,
                relationship_label=definition.relationship,
                is_primary_contact=True,
                is_active=True,
                notes=definition.notes,
            )
            contact.full_clean()
            contact.save()
            _set_created_at(
                contact,
                self.record_origin_at + timedelta(days=3),
            )
            grant = BusinessClientAccessGrant(
                business=business,
                access=self.accesses[definition.business][definition.representative_key],
                business_client=beneficiary,
                authorized_contact=contact,
                relationship_label=definition.relationship,
                is_active=True,
            )
            grant.full_clean()
            grant.save()
            _set_created_at(
                grant,
                self.record_origin_at + timedelta(days=3),
            )

    def _create_closures(self):
        definitions = (
            (
                BUSINESS_MARI,
                3,
                self.future_days[0],
                time(12, 0),
                time(14, 0),
                BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
                "Gestión interna de mostrador.",
            ),
            (
                BUSINESS_MARI,
                None,
                self.future_days[5],
                None,
                None,
                BusinessClosure.ClosureType.LOCAL_HOLIDAY,
                "Festivo local de demostración (no procede del BOE).",
            ),
            (
                BUSINESS_MARI,
                None,
                self.future_days[8],
                None,
                None,
                BusinessClosure.ClosureType.BUSINESS_CLOSURE,
                "Formación interna del equipo.",
            ),
            (
                BUSINESS_NORTE,
                2,
                self.future_days[1],
                time(12, 0),
                time(14, 0),
                BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
                "Mantenimiento de una silla de trabajo.",
            ),
            (
                BUSINESS_NORTE,
                None,
                self.future_days[5],
                None,
                None,
                BusinessClosure.ClosureType.LOCAL_HOLIDAY,
                "Festivo local de demostración (no procede del BOE).",
            ),
        )
        self.closures = []
        for (
            business_key,
            line_number,
            closure_date,
            start_time,
            end_time,
            closure_type,
            reason,
        ) in definitions:
            closure = BusinessClosure(
                business=self.businesses[business_key],
                work_line=(
                    self.lines[business_key][line_number] if line_number is not None else None
                ),
                date_from=closure_date,
                date_to=closure_date,
                start_time=start_time,
                end_time=end_time,
                closure_type=closure_type,
                internal_reason=reason,
                is_active=True,
                created_by=self.professionals[business_key],
            )
            closure.full_clean()
            closure.save()
            _set_created_at(
                closure,
                self.activity_anchor - timedelta(days=1),
            )
            self.closures.append(closure)

    def _create_appointments(self):
        appointments = {}
        for position, definition in enumerate(APPOINTMENTS, start=1):
            business = self.businesses[definition.business]
            professional = self.professionals[definition.business]
            client = self.clients[definition.business][definition.client_key]
            target_date = resolve_day_token(
                definition.day_token,
                past_days=self.past_days,
                future_days=self.future_days,
            )
            starts_at = _at(target_date, time.fromisoformat(definition.start_time))
            minutes = appointment_duration_minutes(definition)
            ends_at = starts_at + timedelta(minutes=minutes)
            services = [
                self.services[definition.business][service_key]
                for service_key in definition.service_keys
            ]
            is_public = definition.channel == Appointment.ManualChannel.PUBLIC_WEB
            requester_access = (
                self.accesses[definition.business][definition.requester_key] if is_public else None
            )
            requester_name = (
                self.clients[definition.business][definition.requester_key].full_name
                if is_public
                else client.full_name
            )
            requester_relationship = (
                BusinessClientAccessGrant.Relationship(definition.requester_relationship).label
                if is_public
                else "Cliente"
            )
            appointment = Appointment(
                business=business,
                business_client=client,
                work_line=self.lines[definition.business][definition.line_number],
                starts_at=starts_at,
                ends_at=ends_at,
                total_duration_minutes=minutes,
                duration_adjustment_reason=definition.duration_adjustment_reason,
                status=definition.status,
                manual_channel=definition.channel,
                created_by=None if is_public else professional,
                requested_by_client_access=requester_access,
                requested_by_name_snapshot=requester_name,
                requested_by_relationship_snapshot=requester_relationship,
                public_confirmation_reference=(
                    uuid.uuid5(
                        DEMO_UUID_NAMESPACE,
                        "|".join(
                            (
                                business.slug,
                                definition.key,
                                starts_at.isoformat(),
                            )
                        ),
                    )
                    if is_public
                    else None
                ),
                cancellation_reason=definition.cancellation_reason,
                service_summary_snapshot=" + ".join(service.name for service in services),
            )
            if definition.status == STATUS_CANCELLED:
                appointment.cancelled_by = professional
                appointment.cancelled_at = starts_at - timedelta(days=1)
            elif definition.status == STATUS_COMPLETED:
                appointment.completed_by = professional
                appointment.completed_at = ends_at + timedelta(minutes=5)
            elif definition.status == STATUS_NO_SHOW:
                appointment.no_show_marked_by = professional
                appointment.no_show_marked_at = ends_at + timedelta(minutes=15)
            appointment.full_clean()
            appointment.save()

            for order, service in enumerate(services, start=1):
                item = AppointmentService(
                    appointment=appointment,
                    service=service,
                    service_name_snapshot=service.name,
                    duration_minutes_snapshot=service.duration_minutes,
                    price_amount_snapshot=service.price_amount,
                    color_hex_snapshot=service.color_hex,
                    display_order=order,
                )
                item.full_clean()
                item.save()
            appointment.full_clean()

            stable_offset = (position % 7) + 1
            created_at = (
                min(
                    starts_at - timedelta(days=stable_offset),
                    self.reference_now - timedelta(minutes=20 + position * 3),
                )
                if starts_at > self.reference_now
                else starts_at - timedelta(days=stable_offset)
            )
            Appointment.objects.filter(pk=appointment.pk).update(
                created_at=created_at,
            )
            appointment.created_at = created_at
            appointments[definition.key] = appointment
        return appointments

    def _create_notifications(self, appointments):
        chosen_keys = (
            "MF01",
            "MF02",
            "MF05",
            "MX04",
            "MP01",
            "MF18",
            "NF01",
            "NF02",
            "NF03",
            "NX02",
            "NP01",
            "NF11",
        )
        for key in chosen_keys:
            appointment = appointments[key]
            event_type = (
                InternalNotification.EventType.APPOINTMENT_CANCELLED
                if appointment.status == Appointment.Status.CANCELLED
                else InternalNotification.EventType.APPOINTMENT_CONFIRMED
            )
            notification = InternalNotification(
                business=appointment.business,
                business_client=appointment.business_client,
                appointment=appointment,
                recipient_user=self.professionals[
                    BUSINESS_MARI
                    if appointment.business_id == self.businesses[BUSINESS_MARI].pk
                    else BUSINESS_NORTE
                ],
                channel=InternalNotification.Channel.INTERNAL,
                event_type=event_type,
                content=(
                    f"Cita cancelada para {appointment.business_client.full_name}."
                    if appointment.status == Appointment.Status.CANCELLED
                    else f"Cita confirmada para {appointment.business_client.full_name}."
                ),
                status=InternalNotification.Status.SIMULATED,
            )
            notification.full_clean()
            notification.save()

    def _create_legal_demo(self):
        profiles = (
            (
                BUSINESS_MARI,
                {
                    "legal_name": "Peluquería Mari · demostración",
                    "tax_identifier": "B00000001",
                    "registered_address": "Calle Mayor 12, Madrid",
                    "privacy_email": "privacidad@peluqueriamari.local",
                    "rights_contact_name": "Mari Profesional",
                    "retention_criteria": (
                        "Durante la relación con el salón y, después, durante los plazos "
                        "necesarios para atender obligaciones y posibles responsabilidades."
                    ),
                },
            ),
            (
                BUSINESS_NORTE,
                {
                    "legal_name": "Barbería Norte · demostración",
                    "tax_identifier": "B00000002",
                    "registered_address": "Avenida Norte 18, Madrid",
                    "privacy_email": "privacidad@barberianorte.local",
                    "rights_contact_name": "Norte Profesional",
                    "retention_criteria": (
                        "Durante la relación con la barbería y, después, durante los plazos "
                        "necesarios para atender obligaciones y posibles responsabilidades."
                    ),
                },
            ),
        )
        for business_key, profile_data in profiles:
            business = self.businesses[business_key]
            if not business.legal_compliance_enabled:
                business.legal_compliance_enabled = True
                business.save(update_fields=["legal_compliance_enabled", "updated_at"])
            accept_professional_legal_documents(
                user=self.professionals[business_key],
                business=business,
                profile_data=profile_data,
                action_fingerprint_source=(f"seed-demo:professional:{business.slug}:legal-v1"),
                accepted_at=self.legal_anchor,
            )

        document = get_active_document(LegalDocument.Kind.CUSTOMER_PRIVACY)
        if document is None:
            raise CommandError("No hay una política de privacidad de clientes vigente.")
        for business_key, business in self.businesses.items():
            for client in BusinessClient.objects.filter(business=business).order_by("pk"):
                access = getattr(client, "access", None)
                action_source = f"seed-demo:customer:{business.slug}:{client.pk}:privacy-v1"
                legal_context = business_legal_snapshot(business)
                if access is not None:
                    acknowledge_customer_privacy(
                        client_access=access,
                        context=LegalAcceptance.Context.CLIENT_REGISTRATION,
                        document=document,
                        legal_context_snapshot=legal_context,
                        action_fingerprint_source=action_source,
                        acknowledged_at=self.legal_anchor,
                    )
                else:
                    record_customer_privacy_information(
                        business_client=client,
                        recorded_by=self.professionals[business_key],
                        channel=CustomerPrivacyEvidence.Channel.IN_PERSON,
                        informed_party_name_snapshot=client.full_name,
                        document=document,
                        legal_context_snapshot=legal_context,
                        action_fingerprint_source=action_source,
                        occurred_at=self.legal_anchor,
                    )

    def _create_activity_events(self, appointments):
        mari = self.businesses[BUSINESS_MARI]
        norte = self.businesses[BUSINESS_NORTE]
        mari_professional = self.professionals[BUSINESS_MARI]
        norte_professional = self.professionals[BUSINESS_NORTE]
        event_rows = (
            (
                mari,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.VISUAL_SETTINGS_UPDATED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Apariencia del espacio profesional actualizada.",
                mari_professional,
                None,
                "business",
                "mari-visual",
                timedelta(days=7),
            ),
            (
                mari,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.NATIONAL_HOLIDAYS_ENABLED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Festivos nacionales del BOE activados en la agenda.",
                mari_professional,
                None,
                "calendar",
                "mari-holidays",
                timedelta(days=6),
            ),
            (
                mari,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.AVAILABILITY_UPDATED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Horario semanal revisado para la próxima quincena.",
                mari_professional,
                None,
                "availability_rule",
                "mari-schedule",
                timedelta(days=5),
            ),
            (
                mari,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.SERVICE_UPDATED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                'Servicio "Color completo" actualizado.',
                mari_professional,
                self.services[BUSINESS_MARI]["full_color"],
                "service",
                "mari-service-update",
                timedelta(days=4),
            ),
            (
                mari,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.SERVICE_PAUSED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                'Servicio "Alisado orgánico" pausado temporalmente.',
                mari_professional,
                self.services[BUSINESS_MARI]["straightening"],
                "service",
                "mari-service-paused",
                timedelta(days=3),
            ),
            (
                mari,
                BusinessActivityEvent.Category.ACCESS,
                BusinessActivityEvent.EventType.CLIENT_ACCESS_ACTIVATED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Cuenta online de Elena Sánchez activada.",
                mari_professional,
                self.accesses[BUSINESS_MARI]["elena"],
                "client_access",
                "mari-access",
                timedelta(days=2),
            ),
            (
                mari,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.CLOSURE_CREATED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Bloqueo puntual creado para la línea 3.",
                mari_professional,
                self.closures[0],
                "closure",
                "mari-closure",
                timedelta(days=1),
            ),
            (
                mari,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_COMPLETED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Cita de María López marcada como atendida.",
                mari_professional,
                appointments["MC21"],
                "appointment",
                "mari-completed",
                timedelta(hours=18),
            ),
            (
                mari,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_NO_SHOW,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Ausencia registrada en una cita anterior.",
                mari_professional,
                appointments["MN04"],
                "appointment",
                "mari-no-show",
                timedelta(hours=12),
            ),
            (
                mari,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_CANCELLED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Reserva online cancelada desde el panel tras solicitar un cambio de fecha.",
                mari_professional,
                appointments["MX04"],
                "appointment",
                "mari-cancelled",
                timedelta(hours=8),
            ),
            (
                mari,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                BusinessActivityEvent.Origin.PHONE,
                f"Cita creada por teléfono para el {appointments['MF03'].starts_at:%d/%m/%Y} a las {appointments['MF03'].starts_at:%H:%M}.",
                mari_professional,
                appointments["MF03"],
                "appointment",
                "mari-phone",
                timedelta(hours=3),
            ),
            (
                mari,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                BusinessActivityEvent.Origin.PUBLIC_WEB,
                f"Reserva online creada para el {appointments['MF01'].starts_at:%d/%m/%Y} a las {appointments['MF01'].starts_at:%H:%M}.",
                None,
                appointments["MF01"],
                "appointment",
                "mari-web",
                timedelta(minutes=25),
            ),
            (
                norte,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.AVAILABILITY_UPDATED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Horario semanal de Barbería Norte actualizado.",
                norte_professional,
                None,
                "availability_rule",
                "norte-schedule",
                timedelta(days=6),
            ),
            (
                norte,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.SERVICE_UPDATED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                'Servicio "Degradado/fade" actualizado.',
                norte_professional,
                self.services[BUSINESS_NORTE]["fade"],
                "service",
                "norte-service-update",
                timedelta(days=5),
            ),
            (
                norte,
                BusinessActivityEvent.Category.CONFIGURATION,
                BusinessActivityEvent.EventType.SERVICE_PAUSED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                'Servicio "Color/mechas" pausado temporalmente.',
                norte_professional,
                self.services[BUSINESS_NORTE]["color"],
                "service",
                "norte-service-paused",
                timedelta(days=4),
            ),
            (
                norte,
                BusinessActivityEvent.Category.ACCESS,
                BusinessActivityEvent.EventType.CLIENT_ACCESS_ACTIVATED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Cuenta online de Álvaro Santos activada.",
                norte_professional,
                self.accesses[BUSINESS_NORTE]["alvaro"],
                "client_access",
                "norte-access",
                timedelta(days=3),
            ),
            (
                norte,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_COMPLETED,
                BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "Cita de Javier Martín marcada como atendida.",
                norte_professional,
                appointments["NC14"],
                "appointment",
                "norte-completed",
                timedelta(days=2),
            ),
            (
                norte,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_CANCELLED,
                BusinessActivityEvent.Origin.EMAIL,
                "Cita cancelada tras recibir un correo del cliente.",
                norte_professional,
                appointments["NX04"],
                "appointment",
                "norte-cancelled",
                timedelta(days=1),
            ),
            (
                norte,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                BusinessActivityEvent.Origin.FRONT_DESK,
                f"Cita creada en mostrador para el {appointments['NF06'].starts_at:%d/%m/%Y} a las {appointments['NF06'].starts_at:%H:%M}.",
                norte_professional,
                appointments["NF06"],
                "appointment",
                "norte-front-desk",
                timedelta(hours=2),
            ),
            (
                norte,
                BusinessActivityEvent.Category.APPOINTMENTS,
                BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                BusinessActivityEvent.Origin.PUBLIC_WEB,
                f"Reserva online creada para el {appointments['NF01'].starts_at:%d/%m/%Y} a las {appointments['NF01'].starts_at:%H:%M}.",
                None,
                appointments["NF01"],
                "appointment",
                "norte-web",
                timedelta(minutes=40),
            ),
        )

        for (
            business,
            category,
            event_type,
            origin,
            summary,
            actor,
            entity,
            entity_type,
            demo_key,
            age,
        ) in sorted(event_rows, key=lambda row: row[-1], reverse=True):
            record_business_activity(
                business=business,
                category=category,
                event_type=event_type,
                origin=origin,
                summary=summary,
                actor=actor,
                actor_type=(
                    BusinessActivityEvent.ActorType.CUSTOMER
                    if actor is None and origin == BusinessActivityEvent.Origin.PUBLIC_WEB
                    else None
                ),
                actor_label=(
                    "Cliente online"
                    if actor is None and origin == BusinessActivityEvent.Origin.PUBLIC_WEB
                    else None
                ),
                entity=entity,
                entity_type=entity_type,
                changes={"demo_seed_key": demo_key},
                event_at=self.activity_anchor - age,
            )

    def _update_client_activity(self):
        for business_key, clients in self.clients.items():
            for client in clients.values():
                latest = (
                    Appointment.objects.filter(
                        business=self.businesses[business_key],
                        business_client=client,
                    )
                    .order_by("-created_at", "-pk")
                    .values_list("created_at", flat=True)
                    .first()
                )
                BusinessClient.objects.filter(pk=client.pk).update(last_activity_at=latest)

    def _validate_postflight(self):
        """Aborta la transacción si la escritura no deja el manifiesto canónico."""

        errors = []

        def expect(label, actual, expected):
            if actual != expected:
                errors.append(f"{label}: esperado {expected!r}, obtenido {actual!r}")

        User = get_user_model()
        expect("usuarios internos", User.objects.count(), 3)
        expect("negocios", Business.objects.count(), 2)
        expect("membresías", BusinessMembership.objects.count(), 2)
        expect("calendarios", BusinessCalendarSettings.objects.count(), 2)
        expect("reglas horarias", AvailabilityRule.objects.count(), 22)
        expect("servicios", Service.objects.count(), 28)
        expect("servicios activos", Service.objects.filter(is_active=True).count(), 25)
        expect("líneas de trabajo", WorkLine.objects.count(), 5)
        expect("clientes", BusinessClient.objects.count(), 36)
        expect("clientes inactivos", BusinessClient.objects.filter(is_active=False).count(), 1)
        expect(
            "clientes sin teléfono", BusinessClient.objects.filter(phone_normalized="").count(), 1
        )
        expect("accesos cliente", BusinessClientAccess.objects.count(), 11)
        expect("accesos activos", BusinessClientAccess.objects.filter(is_active=True).count(), 11)
        expect("permisos de reserva", BusinessClientAccessGrant.objects.count(), 15)
        expect("personas autorizadas", BusinessClientAuthorizedContact.objects.count(), 4)
        expect("cierres", BusinessClosure.objects.count(), 5)
        expect("citas", Appointment.objects.count(), 90)
        expect("snapshots de servicio", AppointmentService.objects.count(), 103)
        expect("notificaciones", InternalNotification.objects.count(), 12)
        expect("movimientos", BusinessActivityEvent.objects.count(), 20)
        expect("perfiles legales", BusinessLegalProfile.objects.count(), 2)
        expect("aceptaciones legales", LegalAcceptance.objects.count(), 17)
        expect("eventos de aceptación", LegalAcceptanceEvent.objects.count(), 17)
        expect("constancias de privacidad", CustomerPrivacyEvidence.objects.count(), 36)
        expect(
            "eventos de privacidad",
            CustomerPrivacyEvidenceEvent.objects.count(),
            36,
        )

        for business_key, business in self.businesses.items():
            expected_services = {
                definition.name for definition in SERVICES if definition.business == business_key
            }
            expected_clients = {
                definition.full_name
                for definition in CLIENTS
                if definition.business == business_key
            }
            expect(
                f"catálogo de {business.slug}",
                set(business.services.values_list("name", flat=True)),
                expected_services,
            )
            expect(
                f"clientes de {business.slug}",
                set(business.clients.values_list("full_name", flat=True)),
                expected_clients,
            )

        invalid_self_grants = [
            grant.pk
            for grant in BusinessClientAccessGrant.objects.filter(
                relationship_label=BusinessClientAccessGrant.Relationship.SELF
            ).select_related("access")
            if grant.access.business_client_id != grant.business_client_id
        ]
        if invalid_self_grants:
            errors.append(
                "permisos titulares vinculados a otra ficha: "
                + ", ".join(map(str, invalid_self_grants))
            )

        status_counts = Counter(Appointment.objects.values_list("status", flat=True))
        expect(
            "estados de cita",
            status_counts,
            Counter(
                {
                    Appointment.Status.COMPLETED: 37,
                    Appointment.Status.NO_SHOW: 6,
                    Appointment.Status.CANCELLED: 9,
                    Appointment.Status.CONFIRMED: 38,
                }
            ),
        )
        confirmed = Appointment.objects.filter(status=Appointment.Status.CONFIRMED)
        expect(
            "confirmadas transcurridas",
            confirmed.filter(ends_at__lte=self.reference_now).count(),
            7,
        )
        expect(
            "confirmadas futuras",
            confirmed.filter(starts_at__gt=self.reference_now).count(),
            31,
        )

        public_appointments = list(
            Appointment.objects.filter(
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB
            ).select_related("requested_by_client_access")
        )
        expect("reservas web", len(public_appointments), 30)
        expect(
            "reservas web representadas",
            sum(
                appointment.requested_by_client_access.business_client_id
                != appointment.business_client_id
                for appointment in public_appointments
                if appointment.requested_by_client_access_id
            ),
            8,
        )
        if any(
            appointment.created_by_id is not None
            or appointment.requested_by_client_access_id is None
            or appointment.public_confirmation_reference is None
            for appointment in public_appointments
        ):
            errors.append("alguna reserva web no conserva su autoría o referencia")
        if (
            Appointment.objects.exclude(manual_channel=Appointment.ManualChannel.PUBLIC_WEB)
            .filter(created_by__isnull=True)
            .exists()
        ):
            errors.append("alguna cita manual carece de profesional creador")
        if AppointmentService.objects.filter(
            service__is_active=False,
            appointment__starts_at__gt=self.reference_now,
        ).exists():
            errors.append("un servicio pausado aparece en una cita futura")

        active_rows = Appointment.objects.exclude(status=Appointment.Status.CANCELLED).order_by(
            "business_id", "work_line_id", "starts_at", "pk"
        )
        previous_by_line = {}
        for appointment in active_rows:
            line_key = (appointment.business_id, appointment.work_line_id)
            previous = previous_by_line.get(line_key)
            if previous is not None and previous.ends_at > appointment.starts_at:
                errors.append(f"solape entre citas {previous.pk} y {appointment.pk}")
            previous_by_line[line_key] = appointment

        for closure in BusinessClosure.objects.filter(is_active=True):
            candidates = Appointment.objects.exclude(status=Appointment.Status.CANCELLED).filter(
                business=closure.business,
                starts_at__date__gte=closure.date_from,
                starts_at__date__lte=closure.date_to,
            )
            if closure.work_line_id:
                candidates = candidates.filter(work_line=closure.work_line)
            for appointment in candidates:
                if closure.start_time is None:
                    errors.append(f"la cita {appointment.pk} coincide con el cierre {closure.pk}")
                    continue
                starts_at = appointment.starts_at.astimezone(MADRID).time()
                ends_at = appointment.ends_at.astimezone(MADRID).time()
                if starts_at < closure.end_time and ends_at > closure.start_time:
                    errors.append(f"la cita {appointment.pk} coincide con el bloqueo {closure.pk}")

        first_appointment_created_at = (
            Appointment.objects.order_by("created_at").values_list("created_at", flat=True).first()
        )
        if first_appointment_created_at is not None:
            if BusinessClient.objects.filter(created_at__gt=first_appointment_created_at).exists():
                errors.append("alguna ficha se creó después de su historial de citas")
            if LegalAcceptance.objects.filter(
                accepted_at__gt=first_appointment_created_at
            ).exists():
                errors.append("alguna aceptación legal es posterior al historial importado")
            if CustomerPrivacyEvidence.objects.filter(
                occurred_at__gt=first_appointment_created_at
            ).exists():
                errors.append("alguna constancia de privacidad es posterior al historial importado")

        if Business.objects.filter(slug="barberia-norte-demo").exists():
            errors.append("persiste el slug heredado barberia-norte-demo")
        if OfficialHoliday.objects.filter(official_reference="PFM-LOCAL").exists():
            errors.append("persiste el antiguo festivo ficticio PFM-LOCAL")

        if errors:
            raise CommandError(
                "El escenario demo no superó el postflight:\n- " + "\n- ".join(errors)
            )


def _update_first_or_create(model, lookup, defaults):
    instance = model.objects.filter(**lookup).first()
    if instance is None:
        instance = model(**lookup)
    for field, value in defaults.items():
        setattr(instance, field, value)
    instance.save()
    return instance


def _set_created_at(instance, value):
    """Fija la fecha narrativa de una entidad demo con ``auto_now_add``."""

    type(instance).objects.filter(pk=instance.pk).update(created_at=value)
    instance.created_at = value


def _acquire_demo_seed_lock():
    """Evita dos regeneraciones simultáneas cuando se usa PostgreSQL."""

    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_xact_lock(%s)",
            [4_147_326_341_001],
        )
        acquired = cursor.fetchone()[0]
    if not acquired:
        raise CommandError("Ya hay otra regeneración del escenario demo en curso.")


def _at(target_date: date, target_time: time) -> datetime:
    return datetime.combine(target_date, target_time, tzinfo=MADRID)

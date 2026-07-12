from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
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
from apps.core.phone import normalize_phone
from apps.core.text import normalize_search_text
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessGrant,
    BusinessClientAuthorizedContact,
)
from apps.holidays.models import HolidaySyncRun, OfficialHoliday
from apps.notifications.models import InternalNotification


DEMO_PASSWORD = "DemoAgendaSalon2026!"
MADRID = ZoneInfo("Europe/Madrid")


class Command(BaseCommand):
    help = "Crea o actualiza datos demo reproducibles para AgendaSalon."

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-date",
            default="",
            help=(
                "Primer lunes de la semana demo en formato YYYY-MM-DD. "
                "Si se omite, se usa el lunes operativo actual o siguiente."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            base_date = (
                date.fromisoformat(options["base_date"])
                if options["base_date"]
                else _current_or_next_monday(timezone.localdate())
            )
        except ValueError as exc:
            raise CommandError("--base-date debe usar formato YYYY-MM-DD.") from exc

        demo = DemoSeeder(base_date=base_date)
        summary = demo.run()

        self.stdout.write(self.style.SUCCESS("Datos demo de AgendaSalon creados o actualizados."))
        self.stdout.write(f"Semana demo: {base_date.isoformat()}")
        self.stdout.write(f"Superadmin: {summary['superadmin_phone']} / {DEMO_PASSWORD}")
        self.stdout.write(f"Profesional: {summary['professional_phone']} / {DEMO_PASSWORD}")
        self.stdout.write(f"Profesional Barbería Norte: {summary['secondary_professional_phone']} / {DEMO_PASSWORD}")
        self.stdout.write(f"Negocio principal: {summary['business']}")
        self.stdout.write(f"Segundo negocio demo: {summary['secondary_business']}")
        self.stdout.write(f"Día sin hueco para 180 min: {summary['no_capacity_date']}")


class DemoSeeder:
    def __init__(self, *, base_date: date):
        self.base_date = base_date
        self.no_capacity_date = base_date + timedelta(days=2)
        self.past_date = min(base_date - timedelta(days=14), timezone.localdate() - timedelta(days=1))

    def run(self):
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
        self.professional = self._upsert_user(
            phone="+34600111001",
            full_name="Mari Profesional",
            email="mari@agendasalon.local",
            is_staff=False,
            is_superuser=False,
        )
        self.secondary_professional = self._upsert_user(
            phone="+34600222001",
            full_name="Norte Profesional",
            email="equipo@barberianorte.local",
            is_staff=False,
            is_superuser=False,
        )
        self.business, self.secondary_business = self._create_businesses()
        self._create_membership()
        self._reset_demo_appointments()
        self._create_calendar()
        self.services = self._create_services()
        self.lines = self._create_work_lines()
        self.clients = self._create_clients()
        self._create_client_accesses()
        self._create_family_booking_demo()
        self._create_holidays_and_closures()
        appointments = self._create_appointments()
        self._create_notifications(appointments)
        self._create_secondary_business_demo()
        self._create_activity_events()

        return {
            "superadmin_phone": self.superadmin.normalized_phone,
            "professional_phone": self.professional.normalized_phone,
            "secondary_professional_phone": self.secondary_professional.normalized_phone,
            "business": self.business.commercial_name,
            "secondary_business": self.secondary_business.commercial_name,
            "no_capacity_date": self.no_capacity_date.isoformat(),
        }

    def _reset_demo_appointments(self):
        InternalNotification.objects.filter(
            business__in=(self.business, self.secondary_business)
        ).delete()
        Appointment.objects.filter(
            business__in=(self.business, self.secondary_business)
        ).delete()

    def _create_activity_events(self):
        events = (
            {
                "business": self.business,
                "category": BusinessActivityEvent.Category.APPOINTMENTS,
                "event_type": BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                "origin": BusinessActivityEvent.Origin.PHONE,
                "summary": "Cita creada por el equipo para el 06/07/2026 a las 10:00.",
                "actor": self.professional,
                "entity_type": "appointment",
                "event_at": _at(self.base_date, time(9, 45)),
            },
            {
                "business": self.business,
                "category": BusinessActivityEvent.Category.APPOINTMENTS,
                "event_type": BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                "origin": BusinessActivityEvent.Origin.PUBLIC_WEB,
                "summary": "Reserva online creada para el 09/07/2026 a las 12:00.",
                "actor_type": BusinessActivityEvent.ActorType.CUSTOMER,
                "actor_label": "Cliente online",
                "entity_type": "appointment",
                "event_at": _at(self.base_date + timedelta(days=3), time(11, 56)),
            },
            {
                "business": self.business,
                "category": BusinessActivityEvent.Category.CONFIGURATION,
                "event_type": BusinessActivityEvent.EventType.SERVICE_UPDATED,
                "origin": BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "summary": 'Servicio "Tinte" actualizado.',
                "actor": self.professional,
                "entity_type": "service",
                "event_at": _at(self.base_date + timedelta(days=1), time(18, 15)),
            },
            {
                "business": self.secondary_business,
                "category": BusinessActivityEvent.Category.APPOINTMENTS,
                "event_type": BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                "origin": BusinessActivityEvent.Origin.FRONT_DESK,
                "summary": "Cita creada por el equipo para el 07/07/2026 a las 17:00.",
                "actor": self.secondary_professional,
                "entity_type": "appointment",
                "event_at": _at(self.base_date + timedelta(days=1), time(16, 42)),
            },
            {
                "business": self.secondary_business,
                "category": BusinessActivityEvent.Category.CONFIGURATION,
                "event_type": BusinessActivityEvent.EventType.AVAILABILITY_UPDATED,
                "origin": BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
                "summary": "Horario actualizado para Viernes de 10:00 a 19:00.",
                "actor": self.secondary_professional,
                "entity_type": "availability_rule",
                "event_at": _at(self.base_date + timedelta(days=2), time(19, 10)),
            },
        )

        for event_data in events:
            existing_event = BusinessActivityEvent.objects.filter(
                business=event_data["business"],
                event_type=event_data["event_type"],
                summary=event_data["summary"],
            ).first()
            if existing_event is None:
                record_business_activity(**event_data)
            else:
                BusinessActivityEvent.objects.filter(pk=existing_event.pk).update(
                    created_at=event_data["event_at"]
                )

    def _upsert_user(self, *, phone, full_name, email, is_staff, is_superuser):
        User = get_user_model()
        normalized_phone = normalize_phone(phone)
        user = User.objects.filter(normalized_phone=normalized_phone).first()
        if user is None:
            user = User(normalized_phone=normalized_phone, phone=phone)
            user.set_password(DEMO_PASSWORD)
        elif not user.has_usable_password():
            user.set_password(DEMO_PASSWORD)
        else:
            # Verifica la credencial demo y actualiza un hash heredado si procede.
            user.check_password(DEMO_PASSWORD)

        user.full_name = full_name
        user.email = email
        user.phone = phone
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.is_active = True
        user.save()
        return user

    def _create_businesses(self):
        mari = _update_first_or_create(
            Business,
            {"slug": "peluqueria-mari"},
            {
                "commercial_name": "Peluquería Mari",
                "public_description": "Salón de belleza con reserva online.",
                "public_phone": "+34 600 111 001",
                "public_email": "hola@peluqueriamari.local",
                "address": "Calle Mayor 12",
                "city": "Madrid",
                "public_booking_enabled": True,
                "public_image_preset": Business.PublicImagePreset.SALON,
                "province": "Madrid",
                "is_active": True,
            },
        )
        Business.objects.filter(slug="barberia-norte-demo").delete()
        barberia = _update_first_or_create(
            Business,
            {"slug": "barberia-norte"},
            {
                "commercial_name": "Barbería Norte",
                "public_description": "Barbería de corte masculino con reserva online.",
                "public_phone": "+34 600 222 001",
                "public_email": "hola@barberianorte.local",
                "address": "Avenida Norte 18",
                "city": "Madrid",
                "public_booking_enabled": True,
                "public_image_preset": Business.PublicImagePreset.BARBERSHOP,
                "province": "Madrid",
                "is_active": True,
            },
        )
        return mari, barberia

    def _create_membership(self):
        _update_first_or_create(
            BusinessMembership,
            {"business": self.business, "user": self.professional},
            {
                "role": BusinessMembership.Role.PROFESSIONAL_ADMIN,
                "is_active": True,
            },
        )
        _update_first_or_create(
            BusinessMembership,
            {"business": self.secondary_business, "user": self.secondary_professional},
            {
                "role": BusinessMembership.Role.PROFESSIONAL_ADMIN,
                "is_active": True,
            },
        )

    def _create_calendar(self):
        _update_first_or_create(
            BusinessCalendarSettings,
            {"business": self.business},
            {
                "slot_interval_minutes": 15,
                "apply_national_holidays": True,
            },
        )

        weekly_rules = {
            0: [(time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))],
            1: [(time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))],
            2: [(time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))],
            3: [(time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))],
            4: [(time(9, 0), time(14, 0)), (time(16, 0), time(20, 0))],
            5: [(time(9, 0), time(14, 0))],
        }
        for weekday, intervals in weekly_rules.items():
            for start_time, end_time in intervals:
                rule = _update_first_or_create(
                    AvailabilityRule,
                    {
                        "business": self.business,
                        "weekday": weekday,
                        "start_time": start_time,
                        "end_time": end_time,
                    },
                    {"is_active": True},
                )
                rule.full_clean()
                rule.save()

    def _create_services(self):
        definitions = [
            ("Lavado", "Lavado y preparación del cabello.", 15, "8.00", "#08927f", True, 1),
            ("Corte", "Corte adaptado al estilo y forma del cabello.", 30, "18.00", "#5079bd", True, 2),
            ("Tinte", "Coloración completa con tiempo de aplicación y acabado.", 90, "48.00", "#d87093", True, 3),
            ("Peinado", "Secado y peinado final.", 45, "26.00", "#d96f5d", True, 4),
            ("Barba", "Perfilado y arreglo de barba.", 30, "14.00", "#8274c9", True, 5),
            ("Tratamiento hidratante", "Cuidado intensivo para hidratar y recuperar el cabello.", 60, "35.00", "#e5a63a", True, 6),
            ("Moldeador clásico", "Moldeado duradero con acabado clásico.", 60, "40.00", "#9a8c84", False, 7),
        ]
        services = {}
        for name, description, duration, price, color, is_active, order in definitions:
            service = _upsert_demo_service(
                business=self.business,
                name=name,
                defaults={
                    "description": description,
                    "duration_minutes": duration,
                    "price_amount": Decimal(price),
                    "color_hex": color,
                    "is_active": is_active,
                    "display_order": order,
                },
            )
            services[name] = service
        return services

    def _create_work_lines(self):
        lines = {}
        for number in (1, 2, 3):
            line = _update_first_or_create(
                WorkLine,
                {"business": self.business, "line_number": number},
                {
                    "name": f"Línea {number}",
                    "is_active": True,
                    "display_order": number,
                },
            )
            line.full_clean()
            line.save()
            lines[number] = line
        return lines

    def _create_clients(self):
        clients = {
            "maria": self._upsert_client("María López", "600111201", "Prefiere citas por la mañana."),
            "lucia": self._upsert_client("Lucía Gómez", "600111202", "Suele reservar varios servicios en la misma visita."),
            "carmen": self._upsert_client("Carmen Ruiz", "600111203", "Agradece confirmar la duración antes de cerrar la cita."),
            "ana": self._upsert_client("Ana Torres", "600111204", "Prefiere las primeras horas de la tarde."),
            "rosa": self._upsert_client("Rosa Martín", "600111205", "Suele pedir cita por teléfono."),
        }
        self._upsert_authorized_contact(
            client=clients["lucia"],
            full_name="Ana Gómez",
            phone="600111244",
            relationship=BusinessClientAuthorizedContact.Relationship.MOTHER,
            is_primary=True,
        )
        return clients

    def _upsert_client(self, full_name, phone, internal_notes, business=None):
        business = business or self.business
        client = BusinessClient.objects.filter(
            business=business,
            full_name_normalized=normalize_search_text(full_name),
            phone_normalized=normalize_phone(phone),
        ).first()
        if client is None:
            client = BusinessClient(business=business)
        client.business = business
        client.full_name = full_name
        client.phone = phone
        client.email = ""
        client.source = BusinessClient.Source.PROFESSIONAL
        client.is_active = True
        client.internal_notes = internal_notes
        client.full_clean()
        client.save()
        return client

    def _upsert_authorized_contact(self, *, client, full_name, phone, relationship, is_primary):
        contact = BusinessClientAuthorizedContact.objects.filter(
            business=self.business,
            business_client=client,
            phone_normalized=normalize_phone(phone),
        ).first()
        if contact is None:
            contact = BusinessClientAuthorizedContact(
                business=self.business,
                business_client=client,
            )
        contact.full_name = full_name
        contact.phone = phone
        contact.relationship_label = relationship
        contact.is_primary_contact = is_primary
        contact.is_active = True
        contact.notes = "Puede pedir cita para esta clienta."
        contact.full_clean()
        contact.save()
        return contact

    def _create_client_accesses(self):
        self._upsert_client_access(self.clients["maria"])
        self._upsert_client_access(self.clients["lucia"])

    def _create_family_booking_demo(self):
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client=self.clients["maria"],
        )
        contact = self._upsert_authorized_contact(
            client=self.clients["rosa"],
            full_name=self.clients["maria"].full_name,
            phone=access.phone,
            relationship=BusinessClientAuthorizedContact.Relationship.FAMILY,
            is_primary=True,
        )
        contact.linked_business_client = self.clients["maria"]
        contact.full_clean()
        contact.save(update_fields=["linked_business_client", "full_name", "phone", "phone_normalized", "updated_at"])
        grant, _ = BusinessClientAccessGrant.objects.update_or_create(
            access=access,
            business_client=self.clients["rosa"],
            defaults={
                "business": self.business,
                "authorized_contact": contact,
                "relationship_label": BusinessClientAccessGrant.Relationship.FAMILY,
                "is_active": True,
            },
        )
        grant.full_clean()
        grant.save()

    def _upsert_client_access(self, client):
        business = client.business
        access = BusinessClientAccess.objects.filter(
            business=business,
            phone_normalized=normalize_phone(client.phone),
        ).first()
        if access is None:
            access = BusinessClientAccess(
                business=business,
                business_client=client,
                phone=client.phone,
            )
        access.business = business
        access.business_client = client
        access.phone = client.phone
        access.is_active = True
        access.set_password(DEMO_PASSWORD)
        access.full_clean()
        access.save()
        BusinessClientAccessGrant.objects.update_or_create(
            access=access,
            business_client=client,
            defaults={
                "business": business,
                "relationship_label": BusinessClientAccessGrant.Relationship.SELF,
                "is_active": True,
            },
        )
        return access

    def _create_secondary_business_demo(self):
        business = self.secondary_business
        _update_first_or_create(
            BusinessCalendarSettings,
            {"business": business},
            {
                "slot_interval_minutes": 15,
                "apply_national_holidays": True,
            },
        )

        weekly_rules = {
            0: [(time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))],
            1: [(time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))],
            2: [(time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))],
            3: [(time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))],
            4: [(time(10, 0), time(14, 0)), (time(16, 0), time(21, 0))],
            5: [(time(10, 0), time(14, 0))],
        }
        for weekday, intervals in weekly_rules.items():
            for start_time, end_time in intervals:
                rule = _update_first_or_create(
                    AvailabilityRule,
                    {
                        "business": business,
                        "weekday": weekday,
                        "start_time": start_time,
                        "end_time": end_time,
                    },
                    {"is_active": True},
                )
                rule.full_clean()
                rule.save()

        service_definitions = [
            ("Corte caballero", "Corte personalizado con acabado y peinado.", 30, "18.00", "#2f6f73", 1),
            ("Degradado", "Degradado trabajado con ajuste de contornos.", 45, "24.00", "#5079bd", 2),
            ("Arreglo de barba", "Recorte, perfilado y acabado de barba.", 30, "14.00", "#8f6b4a", 3),
            ("Corte y barba", "Servicio combinado de corte y arreglo de barba.", 60, "32.00", "#08927f", 4),
            ("Afeitado clásico", "Afeitado tradicional con preparación de la piel.", 45, "26.00", "#e5a63a", 5),
        ]
        for name, description, duration, price, color, order in service_definitions:
            _upsert_demo_service(
                business=business,
                name=name,
                defaults={
                    "description": description,
                    "duration_minutes": duration,
                    "price_amount": Decimal(price),
                    "color_hex": color,
                    "is_active": True,
                    "display_order": order,
                },
            )

        for number in (1, 2):
            line = _update_first_or_create(
                WorkLine,
                {"business": business, "line_number": number},
                {
                    "name": f"Silla {number}",
                    "is_active": True,
                    "display_order": number,
                },
            )
            line.full_clean()
            line.save()

        javier = self._upsert_client("Javier Martín", "600222201", "Suele reservar corte y barba juntos.", business=business)
        marcos = self._upsert_client("Marcos Ruiz", "600222202", "Prefiere las citas a última hora de la tarde.", business=business)
        self._upsert_client_access(javier)

        services = {
            service.name: service
            for service in Service.objects.filter(business=business)
        }
        lines = {
            line.line_number: line
            for line in WorkLine.objects.filter(business=business)
        }
        self._upsert_appointment(
            business=business,
            created_by=self.secondary_professional,
            client=javier,
            line=lines[1],
            start_at=_at(self.past_date, time(10, 0)),
            minutes=60,
            services=[services["Corte y barba"]],
            channel=Appointment.ManualChannel.FRONT_DESK,
            status=Appointment.Status.COMPLETED,
            completed=True,
        )
        self._upsert_appointment(
            business=business,
            created_by=self.secondary_professional,
            client=marcos,
            line=lines[2],
            start_at=_at(self.base_date + timedelta(days=1), time(18, 0)),
            minutes=45,
            services=[services["Degradado"]],
            channel=Appointment.ManualChannel.PHONE,
        )
        self._upsert_appointment(
            business=business,
            created_by=self.secondary_professional,
            client=javier,
            line=lines[1],
            start_at=_at(self.base_date + timedelta(days=3), time(17, 0)),
            minutes=30,
            services=[services["Arreglo de barba"]],
            channel=Appointment.ManualChannel.PUBLIC_WEB,
            status=Appointment.Status.CANCELLED,
            cancellation_reason="El cliente reorganizó su semana.",
        )

    def _create_holidays_and_closures(self):
        holiday_date = self.base_date + timedelta(days=4)
        OfficialHoliday.objects.filter(date=holiday_date, name="Festivo nacional demo").delete()
        HolidaySyncRun.objects.filter(year=holiday_date.year, source_name="Datos demo AgendaSalon").delete()
        _update_first_or_create(
            OfficialHoliday,
            {
                "date": holiday_date,
                "name": "Fiesta nacional",
                "scope": OfficialHoliday.Scope.NATIONAL,
            },
            {
                "year": holiday_date.year,
                "source_name": "Calendario local AgendaSalon",
                "source_url": "",
                "official_reference": "PFM-LOCAL",
            },
        )
        _update_first_or_create(
            HolidaySyncRun,
            {
                "year": holiday_date.year,
                "source_name": "Calendario local AgendaSalon",
            },
            {
                "source_url": "",
                "status": HolidaySyncRun.Status.SUCCESS,
                "started_at": _at(holiday_date - timedelta(days=1), time(8, 0)),
                "finished_at": _at(holiday_date - timedelta(days=1), time(8, 1)),
                "items_loaded": 1,
                "error_detail": "",
                "created_by": self.superadmin,
            },
        )
        _update_first_or_create(
            BusinessClosure,
            {
                "business": self.business,
                "work_line": self.lines[3],
                "date_from": self.base_date,
                "date_to": self.base_date,
                "start_time": time(12, 0),
                "end_time": time(14, 0),
            },
            {
                "closure_type": BusinessClosure.ClosureType.PUNCTUAL_BLOCK,
                "internal_reason": "Gestión interna de mostrador.",
                "is_active": True,
                "created_by": self.professional,
            },
        )
        _update_first_or_create(
            BusinessClosure,
            {
                "business": self.business,
                "work_line": None,
                "date_from": self.base_date + timedelta(days=5),
                "date_to": self.base_date + timedelta(days=5),
                "start_time": None,
                "end_time": None,
            },
            {
                "closure_type": BusinessClosure.ClosureType.BUSINESS_CLOSURE,
                "internal_reason": "Formación interna del equipo.",
                "is_active": True,
                "created_by": self.professional,
            },
        )

    def _create_appointments(self):
        appointments = []
        appointments.append(
            self._upsert_appointment(
                client=self.clients["maria"],
                line=self.lines[1],
                start_at=_at(self.base_date, time(9, 0)),
                minutes=30,
                services=[self.services["Corte"]],
                channel=Appointment.ManualChannel.FRONT_DESK,
            )
        )
        appointments.append(
            self._upsert_appointment(
                client=self.clients["carmen"],
                line=self.lines[1],
                start_at=_at(self.base_date, time(10, 0)),
                minutes=90,
                services=[self.services["Tinte"]],
                channel=Appointment.ManualChannel.PHONE,
            )
        )
        appointments.append(
            self._upsert_appointment(
                client=self.clients["lucia"],
                line=self.lines[2],
                start_at=_at(self.base_date, time(16, 0)),
                minutes=180,
                services=[
                    self.services["Lavado"],
                    self.services["Tinte"],
                    self.services["Corte"],
                    self.services["Peinado"],
                ],
                channel=Appointment.ManualChannel.WHATSAPP,
                summary="Lavado + tinte + corte + peinado",
            )
        )
        appointments.append(
            self._upsert_appointment(
                client=self.clients["ana"],
                line=self.lines[1],
                start_at=_at(self.base_date + timedelta(days=1), time(11, 0)),
                minutes=30,
                services=[self.services["Barba"]],
                channel=Appointment.ManualChannel.PHONE,
                status=Appointment.Status.CANCELLED,
                cancellation_reason="Cliente avisa que no puede acudir.",
            )
        )
        appointments.append(
            self._upsert_appointment(
                client=self.clients["rosa"],
                line=self.lines[1],
                start_at=_at(self.base_date + timedelta(days=1), time(16, 0)),
                minutes=45,
                services=[self.services["Corte"]],
                channel=Appointment.ManualChannel.PHONE,
                duration_adjustment_reason="Margen extra acordado durante la llamada.",
            )
        )
        appointments.append(
            self._upsert_appointment(
                client=self.clients["carmen"],
                line=self.lines[1],
                start_at=_at(self.base_date + timedelta(days=3), time(12, 0)),
                minutes=30,
                services=[self.services["Corte"]],
                channel=Appointment.ManualChannel.PUBLIC_WEB,
            )
        )
        appointments.append(
            self._upsert_appointment(
                client=self.clients["maria"],
                line=self.lines[2],
                start_at=_at(self.past_date, time(10, 0)),
                minutes=60,
                services=[self.services["Tratamiento hidratante"]],
                channel=Appointment.ManualChannel.FRONT_DESK,
                status=Appointment.Status.COMPLETED,
                completed=True,
            )
        )
        appointments.append(
            self._upsert_appointment(
                client=self.clients["lucia"],
                line=self.lines[2],
                start_at=_at(self.past_date, time(12, 0)),
                minutes=30,
                services=[self.services["Corte"]],
                channel=Appointment.ManualChannel.PHONE,
                status=Appointment.Status.NO_SHOW,
                no_show=True,
            )
        )
        appointments.extend(self._create_no_capacity_appointments())
        return appointments

    def _create_no_capacity_appointments(self):
        day = self.no_capacity_date
        definitions = [
            (1, "ana", time(9, 0), 150, ["Tinte", "Tratamiento hidratante"]),
            (1, "rosa", time(12, 0), 120, ["Tinte", "Corte"]),
            (1, "maria", time(16, 0), 120, ["Tinte", "Corte"]),
            (1, "lucia", time(18, 30), 90, ["Tinte"]),
            (2, "carmen", time(9, 0), 90, ["Tinte"]),
            (2, "maria", time(11, 0), 120, ["Tinte", "Corte"]),
            (2, "lucia", time(13, 30), 30, ["Corte"]),
            (2, "ana", time(16, 0), 105, ["Tratamiento hidratante", "Peinado"]),
            (2, "rosa", time(18, 15), 105, ["Tratamiento hidratante", "Peinado"]),
            (3, "carmen", time(9, 0), 180, ["Lavado", "Tinte", "Corte", "Peinado"]),
            (3, "maria", time(16, 0), 180, ["Lavado", "Tinte", "Corte", "Peinado"]),
        ]
        appointments = []
        for line_number, client_key, start_time, minutes, service_names in definitions:
            appointments.append(
                self._upsert_appointment(
                    client=self.clients[client_key],
                    line=self.lines[line_number],
                    start_at=_at(day, start_time),
                    minutes=minutes,
                    services=[self.services[name] for name in service_names],
                    channel=Appointment.ManualChannel.PHONE,
                    summary=" + ".join(service_names),
                )
            )
        return appointments

    def _upsert_appointment(
        self,
        *,
        business=None,
        created_by=None,
        client,
        line,
        start_at,
        minutes,
        services,
        channel,
        status=Appointment.Status.CONFIRMED,
        duration_adjustment_reason="",
        cancellation_reason="",
        completed=False,
        no_show=False,
        summary="",
    ):
        business = business or self.business
        created_by = created_by or self.professional
        appointment = Appointment.objects.filter(
            business=business,
            business_client=client,
            work_line=line,
            starts_at=start_at,
        ).first()
        if appointment is None:
            appointment = Appointment(
                business=business,
                business_client=client,
                work_line=line,
                starts_at=start_at,
            )
        appointment.ends_at = start_at + timedelta(minutes=minutes)
        appointment.total_duration_minutes = minutes
        appointment.status = status
        appointment.manual_channel = channel
        appointment.created_by = created_by
        appointment.duration_adjustment_reason = duration_adjustment_reason
        appointment.cancellation_reason = cancellation_reason
        appointment.service_summary_snapshot = summary or " + ".join(service.name for service in services)
        appointment.cancelled_by = created_by if status == Appointment.Status.CANCELLED else None
        appointment.cancelled_at = start_at - timedelta(days=1) if status == Appointment.Status.CANCELLED else None
        appointment.completed_by = created_by if completed else None
        appointment.completed_at = appointment.ends_at if completed else None
        appointment.no_show_marked_by = created_by if no_show else None
        appointment.no_show_marked_at = appointment.ends_at if no_show else None
        appointment.full_clean()
        appointment.save()

        for order, service in enumerate(services, start=1):
            item = AppointmentService.objects.filter(
                appointment=appointment,
                display_order=order,
            ).first()
            if item is None:
                item = AppointmentService(appointment=appointment, display_order=order)
            item.service = service
            item.service_name_snapshot = service.name
            item.duration_minutes_snapshot = service.duration_minutes
            item.price_amount_snapshot = service.price_amount
            item.color_hex_snapshot = service.color_hex
            item.full_clean()
            item.save()
        appointment.full_clean()
        return appointment

    def _create_notifications(self, appointments):
        for appointment in appointments[:4]:
            event_type = (
                InternalNotification.EventType.APPOINTMENT_CANCELLED
                if appointment.status == Appointment.Status.CANCELLED
                else InternalNotification.EventType.APPOINTMENT_CONFIRMED
            )
            _update_first_or_create(
                InternalNotification,
                {
                    "business": self.business,
                    "appointment": appointment,
                    "event_type": event_type,
                    "channel": InternalNotification.Channel.INTERNAL,
                },
                {
                    "business_client": appointment.business_client,
                    "recipient_user": self.professional,
                    "content": (
                        f"Cita cancelada para {appointment.business_client.full_name}."
                        if appointment.status == Appointment.Status.CANCELLED
                        else f"Cita confirmada para {appointment.business_client.full_name}."
                    ),
                    "status": InternalNotification.Status.SIMULATED,
                    "read_at": None,
                },
            )


def _current_or_next_monday(today):
    return today + timedelta(days=(7 - today.weekday()) % 7)


def _update_first_or_create(model, lookup, defaults):
    instance = model.objects.filter(**lookup).first()
    if instance is None:
        instance = model(**lookup)
    for field, value in defaults.items():
        setattr(instance, field, value)
    instance.save()
    return instance


def _upsert_demo_service(*, business, name, defaults):
    normalized_name = normalize_search_text(name)
    matching_services = [
        service
        for service in Service.objects.filter(business=business).order_by("pk")
        if normalize_search_text(service.name) == normalized_name
    ]

    if matching_services:
        service = max(
            matching_services,
            key=lambda candidate: (
                candidate.appointment_services.exists(),
                candidate.name == name,
                -candidate.pk,
            ),
        )
        for duplicate in matching_services:
            if duplicate.pk == service.pk:
                continue
            duplicate.appointment_services.update(service=service)
            duplicate.delete()
    else:
        service = Service(business=business)

    service.name = name
    for field, value in defaults.items():
        setattr(service, field, value)
    service.full_clean()
    service.save()
    return service


def _at(target_date: date, target_time: time) -> datetime:
    return datetime.combine(target_date, target_time, tzinfo=MADRID)

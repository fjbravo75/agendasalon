from datetime import date, datetime, timedelta
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.booking.models import (
    Appointment,
    AppointmentService,
    AvailabilityRule,
    BusinessCalendarSettings,
    BusinessClosure,
    Service,
    WorkLine,
)
from apps.booking.slot_engine import get_day_availability, suggest_next_slots
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.core.management.commands.seed_demo import DemoSeeder
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessGrant,
    BusinessClientAuthorizedContact,
)
from apps.holidays.models import HolidaySyncRun, OfficialHoliday
from apps.legal.models import (
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)
from apps.legal.services import (
    business_legal_snapshot,
    record_customer_privacy_information,
)
from apps.notifications.models import InternalNotification


MADRID = ZoneInfo("Europe/Madrid")
DEMO_PASSWORD = "DemoAgendaSalon2026!"
DEMO_BUSINESS_SLUGS = ("peluqueria-mari", "barberia-norte")


class SeedDemoCommandTests(TestCase):
    base_date = date(2026, 7, 6)
    reference_now = datetime(2026, 7, 6, 2, 5, tzinfo=MADRID)

    def test_seed_demo_creates_required_demo_data_and_is_idempotent(self):
        scenario = self._run_seed()
        first_counts = self._counts()
        first_legal_fingerprints = self._legal_event_fingerprints()

        self._run_seed()
        second_counts = self._counts()
        second_legal_fingerprints = self._legal_event_fingerprints()

        expected_counts = {
            "users": 3,
            "businesses": 2,
            "memberships": 2,
            "calendar_settings": 2,
            "availability_rules": 22,
            "services": 28,
            "work_lines": 5,
            "clients": 36,
            "client_accesses": 11,
            "access_grants": 15,
            "contacts": 4,
            "closures": 5,
            "holidays": 0,
            "holiday_runs": 0,
            "appointments": 90,
            "appointment_services": 103,
            "notifications": 12,
            "activity_events": 20,
            "legal_acceptances": 17,
            "legal_acceptance_events": 17,
            "customer_privacy_evidence": 36,
            "customer_privacy_evidence_events": 36,
        }
        self.assertEqual(first_counts, expected_counts)
        self.assertEqual(second_counts, expected_counts)
        self.assertEqual(first_legal_fingerprints, second_legal_fingerprints)

        mari = Business.objects.get(slug="peluqueria-mari")
        norte = Business.objects.get(slug="barberia-norte")
        self.assertTrue(mari.is_operational_for_agenda())
        self.assertTrue(norte.is_operational_for_agenda())
        self.assertTrue(mari.public_booking_enabled)
        self.assertTrue(norte.public_booking_enabled)
        self.assertEqual(Business.objects.filter(is_active=True).count(), 2)
        self.assertEqual(Business.objects.filter(slug="barberia-norte-demo").count(), 0)

        self.assertTrue(
            BusinessMembership.objects.filter(
                business=mari,
                user__normalized_phone="+34600111001",
                role=BusinessMembership.Role.PROFESSIONAL_ADMIN,
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            BusinessMembership.objects.filter(
                business=norte,
                user__normalized_phone="+34600222001",
                role=BusinessMembership.Role.PROFESSIONAL_ADMIN,
                is_active=True,
            ).exists()
        )

        self.assertTrue(
            all(
                identify_hasher(user.password).algorithm == "argon2"
                for user in get_user_model().objects.all()
            )
        )
        self.assertTrue(
            all(
                identify_hasher(access.password_hash).algorithm == "argon2"
                for access in BusinessClientAccess.objects.all()
            )
        )

        self.assertEqual(BusinessCalendarSettings.objects.filter(business=mari).count(), 1)
        self.assertEqual(BusinessCalendarSettings.objects.filter(business=norte).count(), 1)
        self.assertEqual(AvailabilityRule.objects.filter(business=mari, is_active=True).count(), 11)
        self.assertEqual(
            AvailabilityRule.objects.filter(business=norte, is_active=True).count(), 11
        )
        self.assertEqual(WorkLine.objects.filter(business=mari, is_active=True).count(), 3)
        self.assertEqual(WorkLine.objects.filter(business=norte, is_active=True).count(), 2)

        self.assertEqual(Service.objects.filter(business=mari).count(), 14)
        self.assertEqual(Service.objects.filter(business=mari, is_active=True).count(), 12)
        self.assertEqual(Service.objects.filter(business=norte).count(), 14)
        self.assertEqual(Service.objects.filter(business=norte, is_active=True).count(), 13)
        self.assertEqual(
            Service.objects.get(business=mari, name="Alisado orgánico").duration_minutes,
            180,
        )
        self.assertFalse(Service.objects.get(business=mari, name="Moldeado permanente").is_active)
        self.assertFalse(Service.objects.get(business=norte, name="Color/mechas").is_active)

        self.assertEqual(BusinessClient.objects.filter(business=mari).count(), 22)
        self.assertEqual(BusinessClient.objects.filter(business=norte).count(), 14)
        self.assertEqual(BusinessClient.objects.filter(is_active=False).count(), 1)
        self.assertEqual(BusinessClient.objects.filter(phone_normalized="").count(), 1)
        self.assertEqual(
            BusinessClient.objects.get(phone_normalized="").full_name,
            "Lucas López",
        )
        self.assertTrue(
            all(
                client.source == BusinessClient.Source.IMPORTED_DEMO
                for client in BusinessClient.objects.all()
            )
        )
        self.assertEqual(BusinessClientAccess.objects.filter(business=mari).count(), 7)
        self.assertEqual(BusinessClientAccess.objects.filter(business=norte).count(), 4)
        self.assertFalse(
            BusinessClientAccess.objects.filter(business_client__full_name="Lucas López").exists()
        )

        expected_relationships = (
            (mari, "Lucas López", "María López", BusinessClientAccessGrant.Relationship.MOTHER),
            (mari, "Rosa Martín", "Daniel Vega", BusinessClientAccessGrant.Relationship.CAREGIVER),
            (
                mari,
                "Teresa García",
                "Isabel Torres",
                BusinessClientAccessGrant.Relationship.DAUGHTER,
            ),
            (norte, "Nico Cabrera", "Óscar Cabrera", BusinessClientAccessGrant.Relationship.FATHER),
        )
        for business, beneficiary_name, representative_name, relationship in expected_relationships:
            with self.subTest(
                business=business.slug,
                beneficiary=beneficiary_name,
                representative=representative_name,
            ):
                beneficiary = BusinessClient.objects.get(
                    business=business,
                    full_name=beneficiary_name,
                )
                representative = BusinessClient.objects.get(
                    business=business,
                    full_name=representative_name,
                )
                contact = BusinessClientAuthorizedContact.objects.get(
                    business=business,
                    business_client=beneficiary,
                    linked_business_client=representative,
                )
                self.assertEqual(contact.relationship_label, relationship)
                self.assertTrue(contact.is_primary_contact)
                self.assertTrue(contact.is_active)
                self.assertTrue(
                    BusinessClientAccessGrant.objects.filter(
                        business=business,
                        access=representative.access,
                        business_client=beneficiary,
                        authorized_contact=contact,
                        relationship_label=relationship,
                        is_active=True,
                    ).exists()
                )

        expected_outcomes = {
            mari: {
                "total": 54,
                Appointment.Status.COMPLETED: 22,
                Appointment.Status.NO_SHOW: 4,
                Appointment.Status.CANCELLED: 5,
                "past_confirmed": 4,
                "future_confirmed": 19,
            },
            norte: {
                "total": 36,
                Appointment.Status.COMPLETED: 15,
                Appointment.Status.NO_SHOW: 2,
                Appointment.Status.CANCELLED: 4,
                "past_confirmed": 3,
                "future_confirmed": 12,
            },
        }
        for business, expected in expected_outcomes.items():
            appointments = Appointment.objects.filter(business=business)
            confirmed = appointments.filter(status=Appointment.Status.CONFIRMED)
            with self.subTest(business=business.slug):
                self.assertEqual(appointments.count(), expected["total"])
                for status in (
                    Appointment.Status.COMPLETED,
                    Appointment.Status.NO_SHOW,
                    Appointment.Status.CANCELLED,
                ):
                    self.assertEqual(
                        appointments.filter(status=status).count(),
                        expected[status],
                    )
                self.assertEqual(
                    confirmed.filter(ends_at__lte=self.reference_now).count(),
                    expected["past_confirmed"],
                )
                self.assertEqual(
                    confirmed.filter(starts_at__gt=self.reference_now).count(),
                    expected["future_confirmed"],
                )
                self.assertEqual(
                    set(appointments.values_list("manual_channel", flat=True)),
                    {
                        Appointment.ManualChannel.PHONE,
                        Appointment.ManualChannel.WHATSAPP,
                        Appointment.ManualChannel.EMAIL,
                        Appointment.ManualChannel.FRONT_DESK,
                        Appointment.ManualChannel.PUBLIC_WEB,
                    },
                )

        self.assertFalse(
            BusinessClient.objects.filter(
                business__in=(mari, norte),
                appointments__isnull=True,
            ).exists()
        )
        for appointment in Appointment.objects.filter(
            manual_channel=Appointment.ManualChannel.PUBLIC_WEB
        ).select_related("requested_by_client_access", "business_client"):
            with self.subTest(appointment=appointment.pk):
                self.assertIsNone(appointment.created_by)
                self.assertIsNotNone(appointment.requested_by_client_access)
                self.assertIsNotNone(appointment.public_confirmation_reference)
                self.assertTrue(appointment.requested_by_name_snapshot)
                self.assertTrue(appointment.requested_by_relationship_snapshot)
                self.assertTrue(
                    BusinessClientAccessGrant.objects.filter(
                        access=appointment.requested_by_client_access,
                        business_client=appointment.business_client,
                        is_active=True,
                    ).exists()
                )

        self.assertEqual(InternalNotification.objects.filter(business=mari).count(), 6)
        self.assertEqual(InternalNotification.objects.filter(business=norte).count(), 6)
        self.assertEqual(BusinessActivityEvent.objects.filter(business=mari).count(), 12)
        self.assertEqual(BusinessActivityEvent.objects.filter(business=norte).count(), 8)
        self.assertTrue(
            all(
                event.created_at <= self.reference_now
                for event in BusinessActivityEvent.objects.all()
            )
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=mari,
                origin=BusinessActivityEvent.Origin.PUBLIC_WEB,
            ).exists()
        )
        first_appointment_created_at = (
            Appointment.objects.order_by("created_at").values_list("created_at", flat=True).first()
        )
        self.assertTrue(
            all(
                client.created_at <= first_appointment_created_at
                for client in BusinessClient.objects.all()
            )
        )
        self.assertTrue(
            all(
                acceptance.accepted_at <= first_appointment_created_at
                for acceptance in LegalAcceptance.objects.all()
            )
        )
        self.assertTrue(
            all(
                evidence.occurred_at <= first_appointment_created_at
                for evidence in CustomerPrivacyEvidence.objects.all()
            )
        )
        self.assertFalse(OfficialHoliday.objects.filter(official_reference="PFM-LOCAL").exists())
        self.assertFalse(
            HolidaySyncRun.objects.filter(
                source_name__in=("Calendario local AgendaSalon", "Datos demo AgendaSalon")
            ).exists()
        )

        no_capacity = get_day_availability(
            business=mari,
            target_date=scenario.no_capacity_date,
            duration_minutes=180,
            now=self.reference_now,
        )
        self.assertFalse(no_capacity.has_slots)
        suggestions = suggest_next_slots(
            business=mari,
            start_date=scenario.no_capacity_date,
            duration_minutes=180,
            now=self.reference_now,
            limit=1,
        )
        self.assertTrue(suggestions)
        self.assertEqual(suggestions[0].starts_at.date(), scenario.future_days[3])

    def test_seed_demo_restores_internal_demo_credentials_and_removes_password_gate(self):
        self._run_seed()
        User = get_user_model()
        demo_phones = (
            "+34910000001",
            "+34600111001",
            "+34600222001",
        )

        for user in User.objects.filter(normalized_phone__in=demo_phones):
            user.set_password("Contraseña modificada durante la prueba 2026")
            user.password_change_required = True
            user.save(update_fields=["password", "password_change_required"])

        changed_access = BusinessClientAccess.objects.get(
            business__slug="peluqueria-mari",
            business_client__full_name="María López",
        )
        changed_access.set_password("Contraseña cliente modificada 2026")
        changed_access.is_pending_public_registration = True
        changed_access.public_registration_expires_at = self.reference_now + timedelta(hours=1)
        changed_access.save(
            update_fields=[
                "password_hash",
                "is_pending_public_registration",
                "public_registration_expires_at",
                "updated_at",
            ]
        )

        self._run_seed()

        restored_users = User.objects.filter(normalized_phone__in=demo_phones)
        self.assertEqual(restored_users.count(), 3)
        for user in restored_users:
            self.assertTrue(user.check_password(DEMO_PASSWORD))
            self.assertFalse(user.check_password("Contraseña modificada durante la prueba 2026"))
            self.assertFalse(user.password_change_required)

        changed_access.refresh_from_db()
        self.assertTrue(changed_access.check_password(DEMO_PASSWORD))
        self.assertFalse(changed_access.is_pending_public_registration)
        self.assertIsNone(changed_access.public_registration_expires_at)

    def test_seed_demo_rebuilds_only_the_fictitious_legal_history(self):
        self._run_seed()
        canonical_counts = self._counts()
        business = Business.objects.get(slug="peluqueria-mari")
        client = BusinessClient.objects.get(
            business=business,
            full_name="Lucas López",
        )
        professional = BusinessMembership.objects.get(
            business=business,
            user__is_superuser=False,
        ).user
        document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        projection = record_customer_privacy_information(
            business_client=client,
            recorded_by=professional,
            channel=CustomerPrivacyEvidence.Channel.OTHER,
            document=document,
            legal_context_snapshot=business_legal_snapshot(business),
        )
        CustomerPrivacyEvidenceEvent.objects.get(
            business_client=client,
            channel=CustomerPrivacyEvidence.Channel.OTHER,
            occurred_at=projection.occurred_at,
        )

        self._run_seed()

        self.assertFalse(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=client,
                channel=CustomerPrivacyEvidence.Channel.OTHER,
            ).exists()
        )
        self.assertEqual(self._counts(), canonical_counts)

    def test_seed_demo_ignores_and_removes_the_legacy_fake_holiday_on_first_run(self):
        OfficialHoliday.objects.create(
            date=date(2026, 7, 10),
            name="Fiesta nacional heredada",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="Datos demo AgendaSalon",
            official_reference="PFM-LOCAL",
        )

        first_scenario = self._run_seed()
        first_dates = tuple(Appointment.objects.order_by("pk").values_list("starts_at", flat=True))
        second_scenario = self._run_seed()
        second_dates = tuple(Appointment.objects.order_by("pk").values_list("starts_at", flat=True))

        self.assertFalse(OfficialHoliday.objects.filter(official_reference="PFM-LOCAL").exists())
        self.assertEqual(first_scenario.past_days, second_scenario.past_days)
        self.assertEqual(first_scenario.future_days, second_scenario.future_days)
        self.assertEqual(first_dates, second_dates)

    def test_seed_demo_aborts_and_rolls_back_when_the_environment_is_not_canonical(self):
        self._run_seed()
        intruder = Business.objects.create(
            commercial_name="Negocio intruso",
            slug="negocio-intruso",
        )
        document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.TERMS,
            is_active=True,
        )
        outside_fingerprint = "f" * 64
        LegalAcceptanceEvent.objects.create(
            document=document,
            business=intruder,
            actor_user=get_user_model().objects.get(is_superuser=True),
            action=LegalAcceptance.Action.ACCEPTED,
            context=LegalAcceptance.Context.PROFESSIONAL_ONBOARDING,
            document_hash_snapshot=document.content_hash,
            legal_context_snapshot={"fixture": "fuera-del-alcance-demo"},
            accepted_at=self.reference_now,
            action_fingerprint=outside_fingerprint,
        )
        appointments_before = tuple(
            Appointment.objects.order_by("pk").values_list("pk", "starts_at")
        )

        with self.assertRaisesMessage(CommandError, "negocios"):
            self._run_seed()

        self.assertTrue(Business.objects.filter(slug="negocio-intruso").exists())
        self.assertTrue(
            LegalAcceptanceEvent.objects.filter(
                action_fingerprint=outside_fingerprint
            ).exists()
        )
        self.assertEqual(
            tuple(Appointment.objects.order_by("pk").values_list("pk", "starts_at")),
            appointments_before,
        )

    def test_seed_demo_with_future_anchor_never_dates_audit_traces_in_future(self):
        future_anchor = self.base_date + timedelta(days=30)

        scenario = self._run_seed(
            base_date=future_anchor,
            reference_now=self.reference_now,
        )

        self.assertEqual(scenario.activity_anchor, self.reference_now)
        self.assertGreaterEqual(scenario.future_days[0], future_anchor)
        self.assertTrue(
            all(
                user.email_verified_at <= self.reference_now
                for user in get_user_model().objects.all()
            )
        )
        self.assertTrue(
            all(
                event.created_at <= self.reference_now
                for event in BusinessActivityEvent.objects.all()
            )
        )
        self.assertTrue(
            all(
                acceptance.accepted_at <= self.reference_now
                for acceptance in LegalAcceptance.objects.all()
            )
        )
        self.assertTrue(
            all(
                evidence.occurred_at <= self.reference_now
                for evidence in CustomerPrivacyEvidence.objects.all()
            )
        )
        self.assertFalse(OfficialHoliday.objects.filter(official_reference="PFM-LOCAL").exists())
        self.assertFalse(
            HolidaySyncRun.objects.filter(
                source_name__in=("Calendario local AgendaSalon", "Datos demo AgendaSalon")
            ).exists()
        )

    def test_seed_demo_merges_legacy_service_name_into_the_canonical_catalog(self):
        self._run_seed()
        business = Business.objects.get(slug="peluqueria-mari")
        Service.objects.create(
            business=business,
            name="Moldeador clasico",
            description="Nombre antiguo",
            duration_minutes=120,
            price_amount="65.00",
            is_active=False,
        )

        self._run_seed()

        self.assertFalse(
            Service.objects.filter(
                business=business,
                name__in=("Moldeador clásico", "Moldeador clasico"),
            ).exists()
        )
        canonical = Service.objects.get(
            business=business,
            name="Moldeado permanente",
        )
        self.assertEqual(canonical.duration_minutes, 120)
        self.assertEqual(canonical.price_amount, 65)
        self.assertFalse(canonical.is_active)
        self.assertEqual(Service.objects.filter(business=business).count(), 14)

    def test_seed_demo_resets_operational_records_when_the_reference_date_changes(self):
        first_scenario = self._run_seed()
        old_appointment_ids = set(Appointment.objects.values_list("id", flat=True))
        old_closure_ids = set(BusinessClosure.objects.values_list("id", flat=True))
        first_future_label = first_scenario.future_days[0].strftime("%d/%m/%Y")

        second_base_date = self.base_date + timedelta(days=7)
        second_reference_now = self.reference_now + timedelta(days=7)
        second_scenario = self._run_seed(
            base_date=second_base_date,
            reference_now=second_reference_now,
        )
        second_future_label = second_scenario.future_days[0].strftime("%d/%m/%Y")

        self.assertFalse(Appointment.objects.filter(pk__in=old_appointment_ids).exists())
        self.assertFalse(BusinessClosure.objects.filter(pk__in=old_closure_ids).exists())
        self.assertEqual(Appointment.objects.count(), 90)
        self.assertEqual(BusinessClosure.objects.count(), 5)
        self.assertEqual(BusinessActivityEvent.objects.count(), 20)
        self.assertFalse(
            BusinessActivityEvent.objects.filter(summary__contains=first_future_label).exists()
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(summary__contains=second_future_label).exists()
        )
        self.assertTrue(
            Appointment.objects.filter(starts_at__date=second_scenario.future_days[0]).exists()
        )
        self.assertFalse(OfficialHoliday.objects.filter(official_reference="PFM-LOCAL").exists())
        self.assertFalse(
            HolidaySyncRun.objects.filter(
                source_name__in=("Calendario local AgendaSalon", "Datos demo AgendaSalon")
            ).exists()
        )

    def _run_seed(self, *, base_date=None, reference_now=None):
        base_date = base_date or self.base_date
        reference_now = reference_now or self.reference_now
        scenario = DemoSeeder(
            anchor_date=base_date,
            reference_now=reference_now,
        )
        with (
            patch(
                "apps.core.management.commands.seed_demo.timezone.now",
                return_value=reference_now,
            ),
            patch(
                "apps.booking.models.timezone.now",
                return_value=reference_now,
            ),
            patch(
                "apps.core.management.commands.seed_demo.timezone.localdate",
                return_value=reference_now.date(),
            ),
        ):
            call_command(
                "seed_demo",
                base_date=base_date.isoformat(),
                stdout=StringIO(),
            )
        return scenario

    def _counts(self):
        User = get_user_model()
        return {
            "users": User.objects.count(),
            "businesses": Business.objects.count(),
            "memberships": BusinessMembership.objects.count(),
            "calendar_settings": BusinessCalendarSettings.objects.count(),
            "availability_rules": AvailabilityRule.objects.count(),
            "services": Service.objects.count(),
            "work_lines": WorkLine.objects.count(),
            "clients": BusinessClient.objects.count(),
            "client_accesses": BusinessClientAccess.objects.count(),
            "access_grants": BusinessClientAccessGrant.objects.count(),
            "contacts": BusinessClientAuthorizedContact.objects.count(),
            "closures": BusinessClosure.objects.count(),
            "holidays": OfficialHoliday.objects.count(),
            "holiday_runs": HolidaySyncRun.objects.count(),
            "appointments": Appointment.objects.count(),
            "appointment_services": AppointmentService.objects.count(),
            "notifications": InternalNotification.objects.count(),
            "activity_events": BusinessActivityEvent.objects.count(),
            "legal_acceptances": LegalAcceptance.objects.count(),
            "legal_acceptance_events": LegalAcceptanceEvent.objects.count(),
            "customer_privacy_evidence": CustomerPrivacyEvidence.objects.count(),
            "customer_privacy_evidence_events": (CustomerPrivacyEvidenceEvent.objects.count()),
        }

    def _legal_event_fingerprints(self):
        return {
            "acceptance": tuple(
                LegalAcceptanceEvent.objects.order_by("action_fingerprint", "pk").values_list(
                    "action_fingerprint", flat=True
                )
            ),
            "customer": tuple(
                CustomerPrivacyEvidenceEvent.objects.order_by(
                    "action_fingerprint",
                    "pk",
                ).values_list("action_fingerprint", flat=True)
            ),
        }

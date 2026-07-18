from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, time
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.booking.models import Appointment, Service
from apps.businesses.models import Business
from apps.core import demo_scenario
from apps.customers.models import BusinessClient, BusinessClientAccess


MARI_CATALOG = (
    ("Lavado y preparación", 15, Decimal("8.00"), True, 1),
    ("Corte mujer", 30, Decimal("22.00"), True, 2),
    ("Secado y peinado", 30, Decimal("20.00"), True, 3),
    ("Peinado cabello largo", 45, Decimal("28.00"), True, 4),
    ("Corte flequillo", 15, Decimal("8.00"), True, 5),
    ("Color raíces", 75, Decimal("38.00"), True, 6),
    ("Color completo", 90, Decimal("50.00"), True, 7),
    ("Baño de color/matiz", 45, Decimal("30.00"), True, 8),
    ("Mechas clásicas", 120, Decimal("70.00"), True, 9),
    ("Balayage/babylights", 150, Decimal("95.00"), True, 10),
    ("Tratamiento hidratante intensivo", 30, Decimal("25.00"), True, 11),
    ("Recogido/peinado evento", 60, Decimal("45.00"), True, 12),
    ("Alisado orgánico", 180, Decimal("160.00"), False, 13),
    ("Moldeado permanente", 120, Decimal("65.00"), False, 14),
)

NORTE_CATALOG = (
    ("Corte clásico", 30, Decimal("18.00"), True, 1),
    ("Degradado/fade", 45, Decimal("20.00"), True, 2),
    ("Corte tijera/cabello largo", 45, Decimal("22.00"), True, 3),
    ("Rapado máquina", 15, Decimal("12.00"), True, 4),
    ("Corte infantil", 30, Decimal("15.00"), True, 5),
    ("Corte mayores 65", 30, Decimal("14.00"), True, 6),
    ("Arreglo/perfilado barba", 30, Decimal("12.00"), True, 7),
    ("Ritual barba toalla caliente", 45, Decimal("18.00"), True, 8),
    ("Afeitado clásico", 30, Decimal("18.00"), True, 9),
    ("Afeitado cabeza", 30, Decimal("16.00"), True, 10),
    ("Contornos/mantenimiento", 15, Decimal("8.00"), True, 11),
    ("Diseño/perfilado cejas", 15, Decimal("6.00"), True, 12),
    ("Camuflaje canas", 45, Decimal("25.00"), True, 13),
    ("Color/mechas", 120, Decimal("40.00"), False, 14),
)

EXPECTED_APPOINTMENT_DISTRIBUTION = {
    "peluqueria-mari": {
        "total": 54,
        Appointment.Status.COMPLETED: 22,
        Appointment.Status.NO_SHOW: 4,
        Appointment.Status.CANCELLED: 5,
        "overdue_confirmed": 4,
        "future_confirmed": 19,
    },
    "barberia-norte": {
        "total": 36,
        Appointment.Status.COMPLETED: 15,
        Appointment.Status.NO_SHOW: 2,
        Appointment.Status.CANCELLED: 4,
        "overdue_confirmed": 3,
        "future_confirmed": 12,
    },
}

EXPECTED_CHANNELS = {
    Appointment.ManualChannel.PHONE,
    Appointment.ManualChannel.WHATSAPP,
    Appointment.ManualChannel.EMAIL,
    Appointment.ManualChannel.FRONT_DESK,
    Appointment.ManualChannel.PUBLIC_WEB,
}


class DemoScenarioPureContractTests(SimpleTestCase):
    def test_visual_and_concurrency_contract_is_explicit_and_stable(self):
        self.assertEqual(
            demo_scenario.CANONICAL_PROFESSIONAL_THEMES,
            {
                demo_scenario.BUSINESS_MARI: "dark",
                demo_scenario.BUSINESS_NORTE: "light",
            },
        )
        self.assertEqual(
            demo_scenario.CANONICAL_PLATFORM_SETTINGS,
            {
                "admin_theme": "light",
                "login_image_preset": "agendasalon",
                "notification_email": "",
                "notification_email_normalized": "",
                "notification_email_verified_at": None,
                "notifications_enabled": True,
                "notify_continuity": True,
                "notify_demo_refresh": True,
                "notify_signup_requests": True,
                "notify_email_failures": True,
            },
        )
        self.assertEqual(demo_scenario.DEMO_ADVISORY_LOCK_ID, 4_147_326_341_001)

    def test_resolve_day_token_accepts_only_available_canonical_tokens(self):
        past_days = (date(2026, 7, 15), date(2026, 7, 14))
        future_days = (date(2026, 7, 17), date(2026, 7, 20))

        self.assertEqual(
            demo_scenario.resolve_day_token(
                "P1",
                past_days=past_days,
                future_days=future_days,
            ),
            past_days[0],
        )
        self.assertEqual(
            demo_scenario.resolve_day_token(
                "F0",
                past_days=past_days,
                future_days=future_days,
            ),
            future_days[0],
        )

        for invalid_token in ("P0", "F-1", "P3", "F2"):
            with self.subTest(token=invalid_token):
                with self.assertRaises(ValueError) as context:
                    demo_scenario.resolve_day_token(
                        invalid_token,
                        past_days=past_days,
                        future_days=future_days,
                    )
                self.assertIn(invalid_token, str(context.exception))

    def test_validation_rejects_duplicate_service_and_client_keys_per_business(self):
        duplicate_service = replace(
            demo_scenario.SERVICES[0],
            description="Duplicado deliberado para probar el contrato.",
        )
        with (
            patch.object(
                demo_scenario,
                "SERVICES",
                demo_scenario.SERVICES + (duplicate_service,),
            ),
            self.assertRaisesRegex(ValueError, "claves de servicio deben ser únicas"),
        ):
            demo_scenario.validate_scenario()

        duplicate_client = replace(
            demo_scenario.CLIENTS[0],
            internal_notes="Duplicado deliberado para probar el contrato.",
        )
        with (
            patch.object(
                demo_scenario,
                "CLIENTS",
                demo_scenario.CLIENTS + (duplicate_client,),
            ),
            self.assertRaisesRegex(ValueError, "claves de cliente deben ser únicas"),
        ):
            demo_scenario.validate_scenario()

    def test_validation_rejects_duplicate_or_inactive_accesses(self):
        duplicate_access = demo_scenario.ACCESSES[0]
        with (
            patch.object(
                demo_scenario,
                "ACCESSES",
                demo_scenario.ACCESSES + (duplicate_access,),
            ),
            self.assertRaisesRegex(ValueError, "solo puede tener un acceso online"),
        ):
            demo_scenario.validate_scenario()

        inactive_accesses = (
            replace(demo_scenario.ACCESSES[0], is_active=False),
            *demo_scenario.ACCESSES[1:],
        )
        with (
            patch.object(demo_scenario, "ACCESSES", inactive_accesses),
            self.assertRaisesRegex(ValueError, "debe estar activo"),
        ):
            demo_scenario.validate_scenario()

    def test_validation_rejects_inconsistent_or_unverified_accesses(self):
        duplicate_email_accesses = (
            demo_scenario.ACCESSES[0],
            replace(
                demo_scenario.ACCESSES[1],
                email=demo_scenario.ACCESSES[0].email.upper(),
            ),
            *demo_scenario.ACCESSES[2:],
        )
        with (
            patch.object(demo_scenario, "ACCESSES", duplicate_email_accesses),
            self.assertRaisesRegex(ValueError, "correos de acceso deben ser únicos"),
        ):
            demo_scenario.validate_scenario()

        missing_client_accesses = (
            replace(demo_scenario.ACCESSES[0], client_key="cliente-inexistente"),
            *demo_scenario.ACCESSES[1:],
        )
        with (
            patch.object(demo_scenario, "ACCESSES", missing_client_accesses),
            self.assertRaisesRegex(ValueError, "cliente inexistente o de otro negocio"),
        ):
            demo_scenario.validate_scenario()

        clients_with_inactive_access_owner = (
            replace(demo_scenario.CLIENTS[0], is_active=False),
            *demo_scenario.CLIENTS[1:],
        )
        with (
            patch.object(
                demo_scenario,
                "CLIENTS",
                clients_with_inactive_access_owner,
            ),
            self.assertRaisesRegex(ValueError, "ficha de cliente está inactiva"),
        ):
            demo_scenario.validate_scenario()

        unverified_accesses = (
            replace(demo_scenario.ACCESSES[0], email_verified=False),
            *demo_scenario.ACCESSES[1:],
        )
        with (
            patch.object(demo_scenario, "ACCESSES", unverified_accesses),
            self.assertRaisesRegex(ValueError, "correo debe estar verificado"),
        ):
            demo_scenario.validate_scenario()

    def test_paused_services_are_historical_only_and_future_use_is_rejected(self):
        services_by_key = {
            (service.business, service.key): service for service in demo_scenario.SERVICES
        }
        paused_appointments = [
            appointment
            for appointment in demo_scenario.APPOINTMENTS
            if any(
                not services_by_key[(appointment.business, service_key)].is_active
                for service_key in appointment.service_keys
            )
        ]
        self.assertTrue(paused_appointments)
        self.assertTrue(
            all(appointment.day_token.startswith("P") for appointment in paused_appointments)
        )

        appointments_with_future_paused_service = tuple(
            replace(appointment, service_keys=("straightening",))
            if appointment.key == "MF19"
            else appointment
            for appointment in demo_scenario.APPOINTMENTS
        )
        with (
            patch.object(
                demo_scenario,
                "APPOINTMENTS",
                appointments_with_future_paused_service,
            ),
            self.assertRaisesRegex(ValueError, "servicio pausado.*citas históricas P"),
        ):
            demo_scenario.validate_scenario()

    def test_online_cancellation_reasons_describe_professional_action(self):
        appointments_by_key = {
            appointment.key: appointment for appointment in demo_scenario.APPOINTMENTS
        }

        for key in ("MX04", "NX02"):
            with self.subTest(appointment=key):
                reason = appointments_by_key[key].cancellation_reason.casefold()
                self.assertIn("solicitó online", reason)
                self.assertIn("canceló la cita", reason)
                self.assertNotIn("cancelada desde la reserva online", reason)
                self.assertNotIn("reprogramada desde la cuenta online", reason)


class DemoScenarioContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.reference_date = timezone.localdate()
        cls.reference_now = timezone.make_aware(
            datetime.combine(cls.reference_date, time(4, 5)),
            timezone.get_current_timezone(),
        )
        cls.base_date = cls.reference_date
        cls._run_seed()

    @classmethod
    def _run_seed(cls):
        with (
            patch(
                "apps.core.management.commands.seed_demo.timezone.now",
                return_value=cls.reference_now,
            ),
            patch(
                "apps.core.management.commands.seed_demo.timezone.localdate",
                return_value=cls.reference_date,
            ),
        ):
            call_command(
                "seed_demo",
                base_date=cls.base_date.isoformat(),
                stdout=StringIO(),
            )

    def test_catalogs_match_the_approved_demo_offer_exactly(self):
        expected_catalogs = {
            "peluqueria-mari": MARI_CATALOG,
            "barberia-norte": NORTE_CATALOG,
        }

        for business_slug, expected in expected_catalogs.items():
            with self.subTest(business=business_slug):
                actual = tuple(
                    Service.objects.filter(business__slug=business_slug)
                    .order_by("display_order", "pk")
                    .values_list(
                        "name",
                        "duration_minutes",
                        "price_amount",
                        "is_active",
                        "display_order",
                    )
                )
                self.assertEqual(actual, expected)

    def test_clients_and_online_accesses_match_the_approved_volume(self):
        expected_counts = {
            "peluqueria-mari": (22, 7),
            "barberia-norte": (14, 4),
        }

        for business_slug, (client_count, access_count) in expected_counts.items():
            with self.subTest(business=business_slug):
                self.assertEqual(
                    BusinessClient.objects.filter(business__slug=business_slug).count(),
                    client_count,
                )
                accesses = BusinessClientAccess.objects.filter(business__slug=business_slug)
                self.assertEqual(accesses.count(), access_count)
                self.assertEqual(accesses.filter(is_active=True).count(), access_count)

        mari_clients = BusinessClient.objects.filter(business__slug="peluqueria-mari")
        self.assertEqual(mari_clients.filter(is_active=False).count(), 1)
        self.assertEqual(mari_clients.filter(phone_normalized="").count(), 1)

        no_phone_client = mari_clients.get(phone_normalized="")
        self.assertEqual(no_phone_client.full_name, "Lucas López")
        self.assertTrue(no_phone_client.is_active)
        self.assertNotEqual(
            no_phone_client.pk,
            mari_clients.get(is_active=False).pk,
        )

    def test_appointments_match_the_approved_outcome_distribution(self):
        global_counts = defaultdict(int)

        for business_slug, expected in EXPECTED_APPOINTMENT_DISTRIBUTION.items():
            appointments = Appointment.objects.filter(business__slug=business_slug)
            confirmed = appointments.filter(status=Appointment.Status.CONFIRMED)
            overdue_confirmed = confirmed.filter(ends_at__lte=self.reference_now)
            future_confirmed = confirmed.filter(starts_at__gte=self.reference_now)

            with self.subTest(business=business_slug):
                self.assertEqual(appointments.count(), expected["total"])
                self.assertEqual(
                    appointments.filter(status=Appointment.Status.COMPLETED).count(),
                    expected[Appointment.Status.COMPLETED],
                )
                self.assertEqual(
                    appointments.filter(status=Appointment.Status.NO_SHOW).count(),
                    expected[Appointment.Status.NO_SHOW],
                )
                self.assertEqual(
                    appointments.filter(status=Appointment.Status.CANCELLED).count(),
                    expected[Appointment.Status.CANCELLED],
                )
                self.assertEqual(
                    overdue_confirmed.count(),
                    expected["overdue_confirmed"],
                )
                self.assertEqual(
                    future_confirmed.count(),
                    expected["future_confirmed"],
                )
                self.assertEqual(
                    confirmed.count(),
                    overdue_confirmed.count() + future_confirmed.count(),
                    "No debe haber citas confirmadas en curso a las 04:05.",
                )

            for status in (
                Appointment.Status.COMPLETED,
                Appointment.Status.NO_SHOW,
                Appointment.Status.CANCELLED,
            ):
                global_counts[status] += appointments.filter(status=status).count()
            global_counts["overdue_confirmed"] += overdue_confirmed.count()
            global_counts["future_confirmed"] += future_confirmed.count()

        self.assertEqual(Appointment.objects.count(), 90)
        self.assertEqual(global_counts[Appointment.Status.COMPLETED], 37)
        self.assertEqual(global_counts[Appointment.Status.NO_SHOW], 6)
        self.assertEqual(global_counts[Appointment.Status.CANCELLED], 9)
        self.assertEqual(global_counts["overdue_confirmed"], 7)
        self.assertEqual(global_counts["future_confirmed"], 31)

    def test_each_business_uses_all_supported_demo_channels(self):
        for business_slug in EXPECTED_APPOINTMENT_DISTRIBUTION:
            with self.subTest(business=business_slug):
                actual_channels = set(
                    Appointment.objects.filter(business__slug=business_slug)
                    .values_list("manual_channel", flat=True)
                    .distinct()
                )
                self.assertEqual(actual_channels, EXPECTED_CHANNELS)

    def test_non_cancelled_appointments_do_not_overlap_on_the_same_line(self):
        appointments_by_line = defaultdict(list)
        appointments = (
            Appointment.objects.exclude(status=Appointment.Status.CANCELLED)
            .select_related("business", "work_line", "business_client")
            .order_by("work_line_id", "starts_at", "ends_at", "pk")
        )
        for appointment in appointments:
            appointments_by_line[appointment.work_line_id].append(appointment)

        for line_id, line_appointments in appointments_by_line.items():
            for previous, current in zip(
                line_appointments,
                line_appointments[1:],
                strict=False,
            ):
                with self.subTest(
                    business=current.business.slug,
                    line=line_id,
                    previous=previous.pk,
                    current=current.pk,
                ):
                    self.assertLessEqual(
                        previous.ends_at,
                        current.starts_at,
                        (
                            f"{previous.business.commercial_name}: "
                            f"{previous.business_client.full_name} y "
                            f"{current.business_client.full_name} se solapan en "
                            f"{current.work_line}."
                        ),
                    )

    def test_appointment_snapshots_and_durations_are_coherent(self):
        appointments = Appointment.objects.select_related(
            "business",
            "business_client",
            "work_line",
        ).prefetch_related("appointment_services__service")

        for appointment in appointments:
            with self.subTest(appointment=appointment.pk):
                elapsed_minutes = int(
                    (appointment.ends_at - appointment.starts_at).total_seconds() // 60
                )
                self.assertEqual(
                    appointment.total_duration_minutes,
                    elapsed_minutes,
                )
                self.assertTrue(appointment.service_summary_snapshot.strip())

                items = list(
                    appointment.appointment_services.order_by(
                        "display_order",
                        "pk",
                    )
                )
                self.assertTrue(items)
                self.assertEqual(
                    [item.display_order for item in items],
                    list(range(1, len(items) + 1)),
                )

                snapshot_minutes = 0
                for item in items:
                    self.assertEqual(item.service.business_id, appointment.business_id)
                    self.assertTrue(item.service_name_snapshot.strip())
                    self.assertGreater(item.duration_minutes_snapshot, 0)
                    self.assertIsNotNone(item.price_amount_snapshot)
                    self.assertGreaterEqual(item.price_amount_snapshot, Decimal("0.00"))
                    self.assertRegex(item.color_hex_snapshot, r"^#[0-9A-Fa-f]{6}$")
                    snapshot_minutes += item.duration_minutes_snapshot

                if snapshot_minutes != appointment.total_duration_minutes:
                    self.assertTrue(
                        appointment.duration_adjustment_reason.strip(),
                        "Todo ajuste de duración debe conservar su motivo.",
                    )

    def test_running_the_seed_twice_keeps_the_same_semantic_scenario(self):
        first_fingerprint = self._scenario_fingerprint()

        self._run_seed()

        second_fingerprint = self._scenario_fingerprint()
        self.assertEqual(second_fingerprint, first_fingerprint)

    @staticmethod
    def _scenario_fingerprint():
        catalogs = tuple(
            Service.objects.select_related("business")
            .filter(business__slug__in=("peluqueria-mari", "barberia-norte"))
            .order_by("business__slug", "display_order", "name", "pk")
            .values_list(
                "business__slug",
                "name",
                "description",
                "duration_minutes",
                "price_amount",
                "color_hex",
                "is_active",
                "display_order",
            )
        )
        clients = tuple(
            BusinessClient.objects.select_related("business")
            .filter(business__slug__in=("peluqueria-mari", "barberia-norte"))
            .order_by(
                "business__slug",
                "full_name_normalized",
                "phone_normalized",
                "pk",
            )
            .values_list(
                "business__slug",
                "full_name",
                "phone_normalized",
                "email",
                "source",
                "is_active",
                "internal_notes",
            )
        )
        accesses = tuple(
            BusinessClientAccess.objects.select_related(
                "business",
                "business_client",
            )
            .filter(business__slug__in=("peluqueria-mari", "barberia-norte"))
            .order_by("business__slug", "phone_normalized", "pk")
            .values_list(
                "business__slug",
                "business_client__full_name",
                "phone_normalized",
                "email_normalized",
                "is_active",
                "email_verified_at",
            )
        )

        appointments = []
        queryset = (
            Appointment.objects.select_related(
                "business",
                "business_client",
                "work_line",
            )
            .prefetch_related("appointment_services")
            .filter(business__slug__in=("peluqueria-mari", "barberia-norte"))
            .order_by(
                "business__slug",
                "starts_at",
                "work_line__line_number",
                "business_client__full_name_normalized",
                "pk",
            )
        )
        for appointment in queryset:
            service_snapshots = tuple(
                appointment.appointment_services.order_by(
                    "display_order",
                    "pk",
                ).values_list(
                    "display_order",
                    "service_name_snapshot",
                    "duration_minutes_snapshot",
                    "price_amount_snapshot",
                    "color_hex_snapshot",
                )
            )
            appointments.append(
                (
                    appointment.business.slug,
                    appointment.business_client.full_name,
                    appointment.work_line.line_number,
                    appointment.starts_at,
                    appointment.ends_at,
                    appointment.total_duration_minutes,
                    appointment.duration_adjustment_reason,
                    appointment.status,
                    appointment.manual_channel,
                    appointment.requested_by_name_snapshot,
                    appointment.requested_by_relationship_snapshot,
                    appointment.cancellation_reason,
                    appointment.service_summary_snapshot,
                    service_snapshots,
                )
            )

        return {
            "catalogs": catalogs,
            "clients": clients,
            "accesses": accesses,
            "appointments": tuple(appointments),
            "businesses": tuple(
                Business.objects.filter(slug__in=("peluqueria-mari", "barberia-norte"))
                .order_by("slug")
                .values_list("slug", "commercial_name", "is_active")
            ),
        }

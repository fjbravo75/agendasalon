from datetime import date, datetime, time, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.template.defaultfilters import date as date_filter
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment, Service
from apps.booking.public_booking_drafts import (
    PUBLIC_BOOKING_DRAFTS_SESSION_KEY,
    PUBLIC_BOOKING_RECEIPTS_SESSION_KEY,
    clear_public_booking_draft,
    clear_public_booking_receipt,
    get_public_booking_draft,
    get_public_booking_receipt_appointment_id,
)
from apps.booking.slot_engine import CHANNEL_PUBLIC, get_booking_options, get_day_availability
from apps.businesses.models import Business, BusinessActivityEvent
from apps.customers.models import BusinessClientAccess, BusinessClientAccessGrant


class AppointmentAssistantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        cls.business = Business.objects.get(slug="peluqueria-mari")
        cls.professional = get_user_model().objects.get(normalized_phone="+34600111001")

    def setUp(self):
        self.slot_now_patcher = patch(
            "apps.booking.slot_engine.timezone.now",
            return_value=self._test_now(),
        )
        self.slot_now_patcher.start()
        self.addCleanup(self.slot_now_patcher.stop)

    def test_appointment_assistant_requires_login(self):
        response = self.client.get(reverse("booking:appointment_assistant"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_appointment_assistant_loads_for_professional(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:appointment_assistant"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nueva cita")
        self.assertContains(response, "Peluquería Mari")
        self.assertContains(response, "Buscar huecos")
        self.assertNotContains(response, "Enlace de reserva online")
        self.assertNotContains(response, "/clientes/peluqueria-mari/entrar/")
        self.assertContains(response, "Lavado - 15 min")
        self.assertNotContains(response, "Lavado (Peluquería Mari)")
        self.assertNotContains(response, "Web publica")
        self.assertNotContains(response, "BusinessClient")
        self.assertNotContains(response, "MVP")
        self.assertContains(response, "Selecciona un cliente")
        self.assertContains(response, "Campos obligatorios")
        self.assertContains(response, 'href="/profesional/agenda/">Volver a la agenda</a>')
        self.assertContains(response, 'class="required-mark"', count=5)
        self.assertContains(response, "service-choice-list--scrollable")
        self.assertContains(response, 'data-service-count="6"')
        self.assertContains(response, 'data-service-count="6" tabindex="0"')
        self.assertContains(response, "data-appointment-search")
        self.assertContains(response, "data-appointment-service", count=6)
        self.assertContains(response, 'data-duration="15"')
        self.assertContains(response, "Duración")
        self.assertContains(response, "Selecciona servicios para calcular el tiempo total.")
        self.assertContains(response, "Ajustar duración")
        self.assertContains(response, "Correo electrónico")
        self.assertContains(response, "Notas internas (opcional)")
        self.assertEqual(response.context["form"]["business_client"].value(), None)

    def test_service_list_only_scrolls_when_more_than_five_services_are_available(self):
        service = self.business.services.filter(is_active=True).order_by("display_order", "pk").last()
        service.is_active = False
        service.save(update_fields=["is_active"])
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:appointment_assistant"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-service-count="5"')
        self.assertNotContains(response, "service-choice-list--scrollable")
        self.assertNotContains(response, 'data-service-count="5" tabindex="0"')

    def test_missing_services_uses_a_compact_actionable_message(self):
        self.client.force_login(self.professional)
        client_id = self.business.clients.get(full_name="Lucía Gómez").id

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "telefono",
                "target_date": "2026-07-09",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecciona al menos un servicio.")
        self.assertNotContains(response, "Este campo es obligatorio.")
        self.assertContains(response, 'class="service-field-errors"')

    def test_partial_search_hides_redundant_required_field_messages(self):
        self.client.force_login(self.professional)
        client_id = self.business.clients.get(full_name="Lucía Gómez").id

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {"business_client": client_id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Selecciona un cliente.")
        self.assertNotContains(response, "Selecciona el canal.")
        self.assertNotContains(response, "Indica el día de la cita.")
        self.assertContains(response, "Selecciona al menos un servicio.")
        self.assertEqual(response.context["form"]["manual_channel"].value(), "telefono")
        self.assertEqual(
            response.context["form"]["target_date"].value(),
            timezone.localdate().isoformat(),
        )

    def test_long_combined_appointment_shows_no_capacity_and_suggestions(self):
        self.client.force_login(self.professional)
        service_ids = self._combined_service_ids()
        client_id = self.business.clients.get(full_name="Lucía Gómez").id

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "whatsapp",
                "services": service_ids,
                "target_date": "2026-07-08",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Duración total")
        self.assertContains(response, "180 min")
        self.assertContains(response, 'data-duration="15"')
        self.assertContains(response, 'data-duration="90"')
        self.assertContains(response, 'data-duration="45"')
        self.assertContains(response, 'value="2026-07-08"')
        self.assertContains(response, "No hay hueco suficiente para 180 min este día.")
        self.assertContains(response, "Otros huecos posibles")
        self.assertContains(response, "Línea")
        self.assertContains(response, "Primera alternativa")
        suggested_date = response.context["recommended_slot"].starts_at
        self.assertContains(response, date_filter(suggested_date, "j F · H:i"))

    def test_available_day_shows_slots_by_work_line(self):
        self.client.force_login(self.professional)
        service_ids = self._combined_service_ids()
        client_id = self.business.clients.get(full_name="Lucía Gómez").id

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "telefono",
                "services": service_ids,
                "target_date": "2026-07-09",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Hay huecos completos para 180 min.")
        self.assertContains(response, "Previsualización")
        self.assertContains(response, "Recomendada")
        self.assertContains(response, "Confirmar cita")

    def test_month_map_uses_real_monday_to_sunday_calendar_alignment(self):
        self.client.force_login(self.professional)
        client_id = self.business.clients.get(full_name="Lucía Gómez").id

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "telefono",
                "services": self._combined_service_ids(),
                "target_date": "2026-07-20",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["month_leading_blanks"]), 2)
        self.assertEqual(len(response.context["month_trailing_blanks"]), 2)
        self.assertContains(response, 'title="Lunes">Lun</abbr>')
        self.assertContains(response, 'title="Miércoles">Mié</abbr>')
        self.assertContains(response, 'title="Domingo">Dom</abbr>')
        self.assertContains(response, "month-day--leading", count=2)
        self.assertContains(response, "month-day--trailing", count=2)
        self.assertContains(response, 'aria-label="miércoles 1 julio')

    def test_holiday_explains_why_the_selected_day_is_closed(self):
        self.client.force_login(self.professional)
        service_ids = self._combined_service_ids()
        client_id = self.business.clients.get(full_name="Lucía Gómez").id

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "telefono",
                "services": service_ids,
                "target_date": "2026-07-10",
            },
        )

        self.assertContains(response, "Este día es festivo nacional y la agenda está cerrada.")
        self.assertNotContains(response, "Desde 17:15")

    def test_professional_can_preview_an_alternative_available_slot(self):
        self.client.force_login(self.professional)
        service_ids = self._combined_service_ids()
        business_client = self.business.clients.get(full_name="Lucía Gómez")
        requested_by = business_client.authorized_contacts.get(full_name="Ana Gómez")
        availability = get_day_availability(
            business=self.business,
            target_date=self._target_date(),
            duration_minutes=180,
        )
        self.assertGreater(len(availability.slots), 1)
        selected_slot = availability.slots[-1]

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": business_client.id,
                "manual_channel": "telefono",
                "requested_by_contact": f"contact:{requested_by.id}",
                "services": service_ids,
                "target_date": self._target_date().isoformat(),
                "selected_work_line_id": selected_slot.work_line_id,
                "selected_starts_at": selected_slot.starts_at.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["recommended_slot"], selected_slot)
        self.assertTrue(response.context["slot_was_selected"])
        self.assertContains(response, "Hora elegida")
        self.assertContains(response, f'value="{selected_slot.starts_at.isoformat()}"')

    def test_professional_can_confirm_recommended_slot(self):
        self.client.force_login(self.professional)
        service_ids = self._combined_service_ids()
        business_client = self.business.clients.get(full_name="Lucía Gómez")
        requested_by = business_client.authorized_contacts.get(full_name="Ana Gómez")
        availability = get_day_availability(
            business=self.business,
            target_date=self._target_date(),
            duration_minutes=180,
        )
        slot = availability.slots[0]

        response = self.client.post(
            reverse("booking:appointment_assistant"),
            {
                "business_client": business_client.id,
                "manual_channel": "telefono",
                "requested_by_contact": f"contact:{requested_by.id}",
                "services": service_ids,
                "target_date": self._target_date().isoformat(),
                "selected_work_line_id": slot.work_line_id,
                "selected_starts_at": slot.starts_at.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Appointment.objects.filter(
                business=self.business,
                business_client=business_client,
                starts_at=slot.starts_at,
                status=Appointment.Status.CONFIRMED,
            ).exists()
        )
        appointment = Appointment.objects.get(
            business=self.business,
            business_client=business_client,
            starts_at=slot.starts_at,
            status=Appointment.Status.CONFIRMED,
        )
        self.assertEqual(appointment.requested_by_name_snapshot, "Ana Gómez")
        self.assertEqual(appointment.requested_by_relationship_snapshot, "Madre")
        self.assertEqual(
            response["Location"],
            reverse(
                "booking:professional_appointment_detail",
                args=[appointment.id],
            ),
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                entity_type="appointment",
                entity_id=appointment.id,
                event_type=BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                origin=BusinessActivityEvent.Origin.PHONE,
                actor_user=self.professional,
            ).exists()
        )

    def test_public_booking_allows_anonymous_service_and_slot_exploration(self):
        service_ids = self._combined_service_ids()

        response = self.client.get(
            reverse("public_booking", args=[self.business.slug]),
            {
                "services": service_ids,
                "target_date": self._target_date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Consulta las horas disponibles sin registrarte")
        self.assertContains(
            response,
            f'href="{reverse("public_booking", args=[self.business.slug])}"',
        )
        self.assertNotContains(response, "Acceso profesional")
        self.assertContains(response, "Elegir esta hora")
        self.assertContains(response, "100,00 €")
        self.assertNotContains(response, "María López")
        self.assertNotContains(response, "600111201")
        self.assertNotContains(response, "Línea")
        self.assertContains(response, "/static/js/public_booking.js")
        self.assertNotContains(response, "<script>\n    (() => {")

    def test_public_service_list_uses_checkboxes_and_only_scrolls_above_five_services(self):
        response = self.client.get(reverse("public_booking", args=[self.business.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-service-count="6"')
        self.assertContains(response, "service-choice-list--scrollable")
        self.assertContains(response, 'data-service-count="6" tabindex="0"')
        self.assertContains(response, 'type="checkbox"', count=6)

        service = self.business.services.filter(is_active=True).order_by("display_order", "pk").last()
        service.is_active = False
        service.save(update_fields=["is_active"])

        response = self.client.get(reverse("public_booking", args=[self.business.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-service-count="5"')
        self.assertContains(response, 'type="checkbox"', count=5)
        self.assertNotContains(response, "service-choice-list--scrollable")
        self.assertNotContains(response, 'data-service-count="5" tabindex="0"')

    def test_public_booking_shows_optimized_options_without_internal_agenda(self):
        self._login_demo_client()
        service_ids = self._combined_service_ids()

        response = self.client.get(
            reverse("public_booking", args=[self.business.slug]),
            {
                "services": service_ids,
                "target_date": self._target_date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reserva online")
        self.assertContains(response, "Elegir esta hora")
        self.assertContains(response, "Reservas como")
        self.assertContains(response, "María López")
        self.assertEqual(response.content.decode().count("María López"), 2)
        self.assertNotContains(response, "Tu nombre")
        self.assertNotContains(response, "Teléfono")
        self.assertNotContains(response, "Línea")

    def test_public_booking_collapses_same_time_across_internal_lines(self):
        self._login_demo_client()
        service_ids = self._combined_service_ids()

        response = self.client.get(
            reverse("public_booking", args=[self.business.slug]),
            {
                "services": service_ids,
                "target_date": self._target_date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        options = get_booking_options(
            business=self.business,
            start_date=self._target_date(),
            duration_minutes=180,
            channel=CHANNEL_PUBLIC,
            limit=4,
        )
        self.assertTrue(options)
        first_public_label = date_filter(options[0].starts_at, "l j, H:i")
        self.assertEqual(response.content.decode().count(first_public_label), 1)

    def test_public_option_uses_the_actual_slot_date_when_it_falls_on_a_later_day(self):
        service_ids = self._combined_service_ids()
        search_date = date(2026, 7, 8)
        options = get_booking_options(
            business=self.business,
            start_date=search_date,
            duration_minutes=180,
            channel=CHANNEL_PUBLIC,
            limit=1,
        )
        self.assertTrue(options)
        self.assertNotEqual(options[0].starts_at.date(), search_date)

        response = self.client.get(
            reverse("public_booking", args=[self.business.slug]),
            {"services": service_ids, "target_date": search_date.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'name="target_date" value="{options[0].starts_at.date().isoformat()}"',
        )

    def test_anonymous_slot_selection_preserves_draft_and_requests_access(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()

        response = self._choose_public_slot(slot, service_ids)

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("customers:client_access", args=[self.business.slug]), response["Location"])
        self.assertIn("confirm%3D1", response["Location"])
        draft = self.client.session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY][str(self.business.id)]
        self.assertEqual(set(draft["service_ids"]), set(service_ids))
        self.assertEqual(draft["selected_work_line_id"], slot.work_line_id)
        self.assertFalse(Appointment.objects.filter(starts_at=slot.starts_at, manual_channel=Appointment.ManualChannel.PUBLIC_WEB).exists())
        access_response = self.client.get(response["Location"])
        self.assertContains(access_response, "Tu hora sigue")
        self.assertContains(access_response, "Entrar y revisar reserva")

    def test_login_resumes_review_and_final_confirmation_uses_same_engine(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"

        login_response = self._login_demo_client(next_url=confirmation_url)
        self.assertEqual(login_response["Location"], confirmation_url)
        review_response = self.client.get(confirmation_url)

        self.assertEqual(review_response.status_code, 200)
        self.assertContains(review_response, "Revisa y confirma")
        self.assertContains(review_response, "María López")
        self.assertContains(review_response, "100,00 €")
        self.assertContains(review_response, "Confirmar cita")
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                business_client__full_name="María López",
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

        response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Appointment.objects.filter(
                business=self.business,
                business_client__full_name="María López",
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
                status=Appointment.Status.CONFIRMED,
            ).exists()
        )
        public_appointment = Appointment.objects.get(
            business=self.business,
            business_client__full_name="María López",
            starts_at=slot.starts_at,
            manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
        )
        public_event = BusinessActivityEvent.objects.get(
            business=self.business,
            entity_type="appointment",
            entity_id=public_appointment.id,
            event_type=BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
        )
        self.assertEqual(public_event.actor_type, BusinessActivityEvent.ActorType.CUSTOMER)
        self.assertEqual(public_event.origin, BusinessActivityEvent.Origin.PUBLIC_WEB)
        self.assertEqual(public_event.actor_label, "Cliente online")
        self.assertNotIn("María López", public_event.summary)
        self.assertNotIn("requested_for", public_event.changes)
        self.assertNotIn("requested_by", public_event.changes)
        self.assertNotIn("María López", str(public_event.changes))
        self.assertNotIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, self.client.session)
        self.assertEqual(
            response["Location"],
            reverse("public_booking_receipt", args=[self.business.slug]),
        )
        self.assertIn(PUBLIC_BOOKING_RECEIPTS_SESSION_KEY, self.client.session)

        receipt_response = self.client.get(response["Location"])
        self.assertEqual(receipt_response.status_code, 200)
        self.assertContains(receipt_response, "Tu cita está confirmada")
        self.assertContains(receipt_response, "María López")
        self.assertContains(receipt_response, "100,00 €")
        self.assertContains(receipt_response, "Confirmación por correo")

        refreshed_response = self.client.get(response["Location"])
        self.assertEqual(refreshed_response.status_code, 200)
        self.assertContains(refreshed_response, "Tu cita está confirmada")

    def test_public_receipt_requires_a_recent_booking_from_the_active_account(self):
        receipt_url = reverse("public_booking_receipt", args=[self.business.slug])

        anonymous_response = self.client.get(receipt_url)
        self.assertEqual(anonymous_response.status_code, 302)
        self.assertIn(
            reverse("customers:client_access", args=[self.business.slug]),
            anonymous_response["Location"],
        )

        self._login_demo_client()
        response_without_receipt = self.client.get(receipt_url)
        self.assertEqual(response_without_receipt.status_code, 302)
        self.assertEqual(
            response_without_receipt["Location"],
            reverse("public_booking", args=[self.business.slug]),
        )

    def test_booking_session_helpers_discard_invalid_and_expired_entries(self):
        business_key = str(self.business.pk)
        now = timezone.now()

        request = SimpleNamespace(
            session={PUBLIC_BOOKING_DRAFTS_SESSION_KEY: {business_key: {"saved_at": "invalid"}}}
        )
        self.assertIsNone(get_public_booking_draft(request, self.business))
        self.assertNotIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, request.session)

        request.session = {
            PUBLIC_BOOKING_DRAFTS_SESSION_KEY: {
                business_key: {"saved_at": now.replace(tzinfo=None).isoformat()}
            }
        }
        self.assertIsNone(get_public_booking_draft(request, self.business))

        expired_draft = {
            "service_ids": [1],
            "target_date": now.date().isoformat(),
            "selected_work_line_id": 1,
            "selected_starts_at": now.isoformat(),
            "saved_at": (now - timedelta(hours=1)).isoformat(),
        }
        request.session = {
            PUBLIC_BOOKING_DRAFTS_SESSION_KEY: {business_key: expired_draft}
        }
        self.assertIsNone(get_public_booking_draft(request, self.business))

        request.session = {}
        clear_public_booking_draft(request, self.business)
        request.session = {
            PUBLIC_BOOKING_DRAFTS_SESSION_KEY: {
                business_key: expired_draft,
                "other": expired_draft,
            }
        }
        clear_public_booking_draft(request, self.business)
        self.assertEqual(
            tuple(request.session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY]),
            ("other",),
        )

        request.session = {
            PUBLIC_BOOKING_RECEIPTS_SESSION_KEY: {
                business_key: {"saved_at": "invalid", "appointment_id": 1}
            }
        }
        self.assertIsNone(
            get_public_booking_receipt_appointment_id(request, self.business)
        )

        request.session = {
            PUBLIC_BOOKING_RECEIPTS_SESSION_KEY: {
                business_key: {
                    "saved_at": (now - timedelta(hours=2)).isoformat(),
                    "appointment_id": 1,
                }
            }
        }
        self.assertIsNone(
            get_public_booking_receipt_appointment_id(request, self.business)
        )

        request.session = {
            PUBLIC_BOOKING_RECEIPTS_SESSION_KEY: {
                business_key: {
                    "saved_at": now.replace(tzinfo=None).isoformat(),
                    "appointment_id": "1",
                }
            }
        }
        self.assertIsNone(
            get_public_booking_receipt_appointment_id(request, self.business)
        )

        request.session = {}
        clear_public_booking_receipt(request, self.business)
        request.session = {
            PUBLIC_BOOKING_RECEIPTS_SESSION_KEY: {
                business_key: {"saved_at": now.isoformat(), "appointment_id": 1},
                "other": {"saved_at": now.isoformat(), "appointment_id": 2},
            }
        }
        clear_public_booking_receipt(request, self.business)
        self.assertEqual(
            tuple(request.session[PUBLIC_BOOKING_RECEIPTS_SESSION_KEY]),
            ("other",),
        )

    def test_public_receipt_rejects_an_appointment_not_owned_by_the_active_account(self):
        self._login_demo_client()
        unrelated_appointment = Appointment.objects.filter(
            business=self.business,
            requested_by_client_access__isnull=True,
        ).first()
        self.assertIsNotNone(unrelated_appointment)
        session = self.client.session
        session[PUBLIC_BOOKING_RECEIPTS_SESSION_KEY] = {
            str(self.business.pk): {
                "appointment_id": unrelated_appointment.pk,
                "saved_at": timezone.now().isoformat(),
            }
        }
        session.save()

        response = self.client.get(
            reverse("public_booking_receipt", args=[self.business.slug])
        )

        self.assertRedirects(
            response,
            reverse("public_booking", args=[self.business.slug]),
            fetch_redirect_response=False,
        )
        self.assertNotIn(PUBLIC_BOOKING_RECEIPTS_SESSION_KEY, self.client.session)

    def test_online_account_can_book_for_an_authorized_family_profile(self):
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="María López",
        )
        beneficiary = self.business.clients.get(full_name="Lucía Gómez")
        BusinessClientAccessGrant.objects.create(
            business=self.business,
            access=access,
            business_client=beneficiary,
            relationship_label=BusinessClientAccessGrant.Relationship.MOTHER,
        )
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        self._login_demo_client(next_url=confirmation_url)

        review_response = self.client.get(confirmation_url)

        self.assertContains(review_response, "¿Para quién es la cita?")
        self.assertContains(review_response, "Lucía Gómez")
        response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking", "business_client": beneficiary.id},
        )

        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.get(
            business=self.business,
            business_client=beneficiary,
            starts_at=slot.starts_at,
            manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
        )
        self.assertEqual(appointment.requested_by_client_access, access)
        self.assertEqual(appointment.requested_by_name_snapshot, "María López")
        self.assertEqual(appointment.requested_by_relationship_snapshot, "Madre")

    def test_online_account_cannot_book_for_an_ungranted_profile(self):
        unauthorized_client = self.business.clients.get(full_name="Carmen Ruiz")
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        self._login_demo_client(next_url=confirmation_url)

        response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking", "business_client": unauthorized_client.id},
            follow=True,
        )

        self.assertContains(response, "Ya no tienes permiso para reservar para esa persona.")
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                business_client=unauthorized_client,
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

    def test_authenticated_selection_still_requires_explicit_confirmation(self):
        self._login_demo_client()
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()

        response = self._choose_public_slot(slot, service_ids)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"{reverse('public_booking', args=[self.business.slug])}?confirm=1",
        )
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                business_client__full_name="María López",
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

    def test_expired_public_draft_returns_to_search_without_creating_appointment(self):
        self._login_demo_client()
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        session = self.client.session
        drafts = session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY]
        drafts[str(self.business.id)]["saved_at"] = (
            timezone.now() - timedelta(minutes=31)
        ).isoformat()
        session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY] = drafts
        session.save()

        response = self.client.get(
            reverse("public_booking", args=[self.business.slug]),
            {"confirm": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La selección ha caducado")
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                business_client__full_name="María López",
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

    def test_slot_is_revalidated_before_review_and_returns_alternatives_if_taken(self):
        self._login_demo_client()
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        Appointment.objects.create(
            business=self.business,
            business_client=self.business.clients.get(full_name="Lucía Gómez"),
            work_line_id=slot.work_line_id,
            starts_at=slot.starts_at,
            ends_at=slot.starts_at + timedelta(minutes=180),
            total_duration_minutes=180,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            service_summary_snapshot="Bloqueo de prueba",
        )

        response = self.client.get(
            reverse("public_booking", args=[self.business.slug]),
            {"confirm": "1"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Esa hora acaba de ocuparse")
        self.assertContains(response, "Horas disponibles")
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                business_client__full_name="María López",
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

    def _combined_service_ids(self):
        return list(
            Service.objects.filter(
                business=self.business,
                name__in=["Lavado", "Tinte", "Corte", "Peinado"],
            )
            .order_by("display_order")
            .values_list("id", flat=True)
        )

    def _first_public_slot(self):
        return get_booking_options(
            business=self.business,
            start_date=self._target_date(),
            duration_minutes=180,
            channel=CHANNEL_PUBLIC,
            limit=1,
        )[0]

    def _choose_public_slot(self, slot, service_ids):
        return self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {
                "action": "choose_slot",
                "services": service_ids,
                "target_date": self._target_date().isoformat(),
                "selected_work_line_id": slot.work_line_id,
                "selected_starts_at": slot.starts_at.isoformat(),
            },
        )

    def _login_demo_client(self, *, next_url=None):
        response = self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {
                "action": "login",
                "next": next_url or reverse("public_booking", args=[self.business.slug]),
                "phone": "600111201",
                "password": "DemoAgendaSalon2026!",
            },
        )
        self.assertEqual(response.status_code, 302)
        return response

    def _target_date(self):
        return date(2026, 7, 9)

    def _test_now(self):
        return datetime.combine(
            self._target_date(),
            time(8, 0),
            tzinfo=ZoneInfo("Europe/Madrid"),
        )

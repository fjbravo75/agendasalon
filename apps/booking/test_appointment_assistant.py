from datetime import date, datetime, time, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.template.defaultfilters import date as date_filter
from django.urls import reverse
from django.utils import timezone

from apps.booking.calendar_locking import (
    lock_business_calendar as real_lock_business_calendar,
)
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
from apps.customers.forms import ProfessionalClientQuickForm
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessGrant,
)
from apps.legal.models import (
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    LegalAcceptanceEvent,
    LegalDocument,
)
from apps.legal.presentations import LEGAL_PRESENTATION_CHANGED_MESSAGE
from apps.legal.services import EVENT_FINGERPRINT_COLLISION_MESSAGE
from apps.holidays.models import OfficialHoliday


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
        self.assertContains(response, "Lavado y preparación - 15 min")
        self.assertNotContains(response, "Lavado y preparación (Peluquería Mari)")
        self.assertNotContains(response, "Web publica")
        self.assertNotContains(response, "BusinessClient")
        self.assertNotContains(response, "MVP")
        self.assertContains(response, "Selecciona un cliente")
        self.assertContains(response, "Campos obligatorios")
        self.assertContains(response, 'href="/profesional/agenda/">Volver a la agenda</a>')
        self.assertContains(response, 'class="required-mark"', count=5)
        self.assertContains(response, "service-choice-list--scrollable")
        self.assertContains(response, 'data-service-count="12"')
        self.assertContains(response, 'data-service-count="12" tabindex="0"')
        self.assertContains(
            response,
            "12 servicios disponibles. Desplázate para ver el catálogo completo.",
        )
        self.assertContains(response, 'aria-describedby="services-scroll-hint"')
        self.assertContains(response, "data-appointment-search")
        self.assertContains(response, 'id="appointment-search-form"')
        self.assertContains(response, "data-appointment-results-stale")
        self.assertContains(response, 'data-results-actionable="false"')
        self.assertContains(response, "Buscar con estos cambios")
        self.assertContains(response, "data-appointment-service", count=12)
        self.assertContains(response, 'id="appointment-requester-options"')
        lucas = self.business.clients.get(full_name="Lucas L\u00f3pez")
        mother = lucas.authorized_contacts.get(full_name="Mar\u00eda L\u00f3pez")
        self.assertEqual(
            response.context["requester_choices_by_client"][str(lucas.pk)],
            [
                {"value": "self", "label": "Lucas L\u00f3pez (para s\u00ed)"},
                {
                    "value": f"contact:{mother.pk}",
                    "label": "Mar\u00eda L\u00f3pez \u00b7 Madre",
                },
            ],
        )
        self.assertContains(response, 'data-duration="15"')
        self.assertContains(response, "Duración")
        self.assertContains(response, "Selecciona servicios para calcular el tiempo total.")
        self.assertContains(response, "Ajustar duración")
        self.assertContains(response, "Correo electrónico")
        self.assertContains(response, "Notas internas (opcional)")
        self.assertEqual(response.context["form"]["business_client"].value(), None)

    def test_appointment_assistant_redirects_to_setup_when_business_is_not_operational(self):
        self.business.services.update(is_active=False)
        self.business.work_lines.update(is_active=False)
        self.business.availability_rules.update(is_active=False)
        self.client.force_login(self.professional)

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            follow=True,
        )

        self.assertRedirects(response, reverse("dashboards:professional_home"))
        self.assertContains(
            response,
            "Completa los servicios, las líneas de trabajo y el horario antes de crear una cita.",
        )
        self.assertContains(response, "Pon tu agenda en marcha")

    def test_service_list_only_scrolls_when_more_than_five_services_are_available(self):
        services_to_pause = list(
            self.business.services.filter(is_active=True)
            .order_by("display_order", "pk")
            .values_list("pk", flat=True)[5:]
        )
        self.business.services.filter(pk__in=services_to_pause).update(is_active=False)
        self.client.force_login(self.professional)

        response = self.client.get(reverse("booking:appointment_assistant"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-service-count="5"')
        self.assertNotContains(response, "service-choice-list--scrollable")
        self.assertNotContains(response, 'data-service-count="5" tabindex="0"')
        self.assertNotContains(response, "servicios disponibles. Desplázate")
        self.assertNotContains(response, 'id="services-scroll-hint"')

    def test_assistant_quick_client_pauses_without_a_privacy_document(self):
        document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        document.is_active = False
        document.save(update_fields=["is_active"])
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")
        client_count = self.business.clients.count()

        page = self.client.get(assistant_url)
        response = self.client.post(
            assistant_url,
            {
                "action": "quick_client",
                "full_name": "Cliente no guardado",
                "phone": "600333229",
                "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "privacy_information_provided": "on",
                "legal_presentation_token": "",
            },
        )

        for result in (page, response):
            with self.subTest(method=result.request["REQUEST_METHOD"]):
                self.assertEqual(result.status_code, 200)
                self.assertFalse(
                    result.context["quick_privacy_document_available"]
                )
                self.assertEqual(
                    result.context["quick_legal_presentation_token"],
                    "",
                )
                self.assertContains(
                    result,
                    "Creación temporalmente no disponible",
                )
                self.assertContains(
                    result,
                    "No hemos guardado ningún dato",
                    count=1,
                )
                self.assertNotContains(result, ">Guardar cliente</button>")
        self.assertContains(response, "Cliente no guardado")
        self.assertEqual(self.business.clients.count(), client_count)
        self.assertFalse(
            self.business.clients.filter(phone_normalized="+34600333229").exists()
        )

    def test_assistant_reused_quick_client_rejects_different_optional_data_before_evidence(self):
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")

        for index, changed_field in enumerate(("email", "internal_notes"), start=1):
            with self.subTest(changed_field=changed_field):
                existing = BusinessClient.objects.create(
                    business=self.business,
                    full_name=f"Cliente asistente existente {index}",
                    phone=f"60033346{index}",
                    email=f"asistente{index}@example.com",
                    internal_notes=f"Notas asistente {index}",
                    source=BusinessClient.Source.PROFESSIONAL,
                )
                page = self.client.get(assistant_url)
                payload = {
                    "action": "quick_client",
                    "full_name": existing.full_name,
                    "phone": existing.phone,
                    "email": existing.email,
                    "internal_notes": existing.internal_notes,
                    "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
                    "privacy_information_provided": "on",
                    "legal_presentation_token": page.context[
                        "quick_legal_presentation_token"
                    ],
                }
                payload[changed_field] = (
                    f"asistente-distinto{index}@example.com"
                    if changed_field == "email"
                    else f"Notas asistente distintas {index}"
                )

                response = self.client.post(assistant_url, payload)

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    "el correo o las notas no coinciden",
                )
                self.assertEqual(
                    CustomerPrivacyEvidenceEvent.objects.filter(
                        business_client=existing
                    ).count(),
                    0,
                )
                self.assertEqual(
                    CustomerPrivacyEvidence.objects.filter(
                        business_client=existing
                    ).count(),
                    0,
                )
                existing.refresh_from_db()
                self.assertEqual(existing.email, f"asistente{index}@example.com")
                self.assertEqual(
                    existing.internal_notes,
                    f"Notas asistente {index}",
                )

    def test_assistant_reused_quick_client_exact_replay_is_idempotent(self):
        self.client.force_login(self.professional)
        existing = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente asistente replay",
            phone="600333469",
            email="asistente-replay@example.com",
            internal_notes="Datos del asistente ya guardados.",
            source=BusinessClient.Source.PROFESSIONAL,
        )
        assistant_url = reverse("booking:appointment_assistant")
        page = self.client.get(assistant_url)
        payload = {
            "action": "quick_client",
            "full_name": existing.full_name,
            "phone": existing.phone,
            "email": existing.email,
            "internal_notes": existing.internal_notes,
            "privacy_channel": CustomerPrivacyEvidence.Channel.PHONE,
            "privacy_information_provided": "on",
            "legal_presentation_token": page.context[
                "quick_legal_presentation_token"
            ],
        }

        first = self.client.post(assistant_url, payload)
        second = self.client.post(assistant_url, payload)

        expected_location = (
            f"{assistant_url}?{urlencode({'business_client': existing.pk})}"
        )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(first["Location"], expected_location)
        self.assertEqual(second["Location"], expected_location)
        self.assertEqual(
            BusinessClient.objects.filter(
                business=self.business,
                full_name=existing.full_name,
                phone_normalized=existing.phone_normalized,
            ).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidenceEvent.objects.filter(
                business_client=existing
            ).count(),
            1,
        )
        self.assertEqual(
            CustomerPrivacyEvidence.objects.filter(
                business_client=existing
            ).count(),
            1,
        )

    def test_quick_client_keeps_privacy_confirmation_when_other_data_is_invalid(self):
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")
        page = self.client.get(assistant_url)

        response = self.client.post(
            assistant_url,
            {
                "action": "quick_client",
                "full_name": "",
                "phone": "600333224",
                "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "privacy_information_provided": "on",
                "legal_presentation_token": page.context[
                    "quick_legal_presentation_token"
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["quick_client_form"][
                "privacy_information_provided"
            ].value(),
            True,
        )
        self.assertFalse(
            self.business.clients.filter(phone_normalized="+34600333224").exists()
        )

    def test_quick_client_clears_privacy_confirmation_if_document_changed(self):
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")
        page = self.client.get(assistant_url)
        old_token = page.context["quick_legal_presentation_token"]
        old_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        old_document.is_active = False
        old_document.save(update_fields=["is_active"])
        LegalDocument.objects.create(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            slug="privacidad-clientes-alta-rapida-v2",
            version="test-alta-rapida-v2",
            title="Privacidad de clientes",
            lead="Información actualizada para la prueba.",
            sections=[{"heading": "Responsable", "body": "Versión vigente."}],
            is_active=True,
        )

        response = self.client.post(
            assistant_url,
            {
                "action": "quick_client",
                "full_name": "",
                "phone": "600333225",
                "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                "privacy_information_provided": "on",
                "legal_presentation_token": old_token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'role="alert" tabindex="-1" data-error-summary',
        )
        self.assertFalse(
            response.context["quick_client_form"][
                "privacy_information_provided"
            ].value()
        )
        self.assertNotEqual(
            response.context["quick_legal_presentation_token"],
            old_token,
        )
        self.assertContains(response, "versión test-alta-rapida-v2")
        self.assertFalse(
            self.business.clients.filter(phone_normalized="+34600333225").exists()
        )

    def test_quick_client_replay_with_changed_data_renews_confirmation(self):
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")
        page = self.client.get(assistant_url)
        old_token = page.context["quick_legal_presentation_token"]
        original_payload = {
            "action": "quick_client",
            "full_name": "Cliente alta repetida",
            "phone": "600333230",
            "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
            "privacy_information_provided": "on",
            "legal_presentation_token": old_token,
        }

        first_response = self.client.post(assistant_url, original_payload)
        self.assertEqual(first_response.status_code, 302)
        client_count = self.business.clients.count()
        event_count = CustomerPrivacyEvidenceEvent.objects.count()

        response = self.client.post(
            assistant_url,
            {
                **original_payload,
                "full_name": "Cliente alta alterada",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, EVENT_FINGERPRINT_COLLISION_MESSAGE)
        self.assertContains(response, "Cliente alta alterada")
        self.assertFalse(
            response.context["quick_client_form"][
                "privacy_information_provided"
            ].value()
        )
        self.assertNotEqual(
            response.context["quick_legal_presentation_token"],
            old_token,
        )
        self.assertEqual(self.business.clients.count(), client_count)
        self.assertEqual(CustomerPrivacyEvidenceEvent.objects.count(), event_count)
        self.assertFalse(
            self.business.clients.filter(full_name="Cliente alta alterada").exists()
        )

    def test_quick_client_rechecks_privacy_before_rerendering_invalid_data(self):
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")
        page = self.client.get(assistant_url)
        old_token = page.context["quick_legal_presentation_token"]
        original_validate = ProfessionalClientQuickForm.validate_legal_presentation

        def validate_then_rotate(form, **kwargs):
            receipt = original_validate(form, **kwargs)
            old_document = LegalDocument.objects.get(
                kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
                is_active=True,
            )
            old_document.is_active = False
            old_document.save(update_fields=["is_active"])
            LegalDocument.objects.create(
                kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
                slug="privacidad-clientes-carrera-alta-rapida",
                version="test-carrera-alta-rapida",
                title="Privacidad de clientes",
                lead="Información actualizada durante la validación.",
                sections=[{"heading": "Responsable", "body": "Versión vigente."}],
                is_active=True,
            )
            return receipt

        with patch.object(
            ProfessionalClientQuickForm,
            "validate_legal_presentation",
            new=validate_then_rotate,
        ):
            response = self.client.post(
                assistant_url,
                {
                    "action": "quick_client",
                    "full_name": "",
                    "phone": "600333226",
                    "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                    "privacy_information_provided": "on",
                    "legal_presentation_token": old_token,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            response.context["quick_client_form"][
                "privacy_information_provided"
            ].value()
        )
        self.assertNotEqual(
            response.context["quick_legal_presentation_token"],
            old_token,
        )
        self.assertContains(response, "versión test-carrera-alta-rapida")
        self.assertFalse(
            self.business.clients.filter(phone_normalized="+34600333226").exists()
        )

    def test_quick_client_rerender_uses_current_compliance_if_it_was_enabled(self):
        self.business.legal_compliance_enabled = False
        self.business.save(update_fields=["legal_compliance_enabled", "updated_at"])
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")
        page = self.client.get(assistant_url)
        self.assertEqual(page.context["quick_legal_presentation_token"], "")
        original_validate = ProfessionalClientQuickForm.validate_legal_presentation

        def enable_compliance_then_validate(form, **kwargs):
            Business.objects.filter(pk=self.business.pk).update(
                legal_compliance_enabled=True
            )
            return original_validate(form, **kwargs)

        with patch.object(
            ProfessionalClientQuickForm,
            "validate_legal_presentation",
            new=enable_compliance_then_validate,
        ):
            response = self.client.post(
                assistant_url,
                {
                    "action": "quick_client",
                    "full_name": "Nuria Cambio Legal",
                    "phone": "600333227",
                    "legal_presentation_token": "",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["business"].legal_compliance_enabled)
        self.assertTrue(response.context["quick_legal_presentation_token"])
        self.assertContains(response, "Información al cliente")
        self.assertFalse(
            self.business.clients.filter(phone_normalized="+34600333227").exists()
        )

    def test_quick_client_rerender_hides_obsolete_compliance_if_it_was_disabled(self):
        self.client.force_login(self.professional)
        assistant_url = reverse("booking:appointment_assistant")
        page = self.client.get(assistant_url)
        self.assertTrue(page.context["quick_legal_presentation_token"])
        original_validate = ProfessionalClientQuickForm.validate_legal_presentation

        def disable_compliance_then_validate(form, **kwargs):
            Business.objects.filter(pk=self.business.pk).update(
                legal_compliance_enabled=False
            )
            return original_validate(form, **kwargs)

        with patch.object(
            ProfessionalClientQuickForm,
            "validate_legal_presentation",
            new=disable_compliance_then_validate,
        ):
            response = self.client.post(
                assistant_url,
                {
                    "action": "quick_client",
                    "full_name": "",
                    "phone": "600333228",
                    "privacy_channel": CustomerPrivacyEvidence.Channel.IN_PERSON,
                    "privacy_information_provided": "on",
                    "legal_presentation_token": page.context[
                        "quick_legal_presentation_token"
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["business"].legal_compliance_enabled)
        self.assertEqual(response.context["quick_legal_presentation_token"], "")
        self.assertNotContains(response, "Información al cliente")
        self.assertFalse(
            self.business.clients.filter(phone_normalized="+34600333228").exists()
        )

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
        self.assertContains(
            response,
            'aria-describedby="services-error services-scroll-hint"',
        )

    def test_agenda_prefill_keeps_date_and_time_without_premature_errors(self):
        self.client.force_login(self.professional)
        selected_start = "2026-07-09T10:30:00+02:00"

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "prefill_from_agenda": "1",
                "target_date": "2026-07-09",
                "selected_work_line_id": "2",
                "selected_starts_at": selected_start,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_search"])
        self.assertTrue(response.context["agenda_prefill"])
        self.assertFalse(response.context["form"].is_bound)
        self.assertEqual(response.context["form"]["target_date"].value(), "2026-07-09")
        self.assertEqual(response.context["selected_work_line_id"], "2")
        self.assertEqual(response.context["selected_starts_at"], selected_start)
        self.assertContains(response, "Completa la cita para comprobar esa hora")
        self.assertContains(response, "Al buscar, volveremos a comprobar")
        self.assertNotContains(response, "Selecciona al menos un servicio.")
        self.assertNotContains(response, "Falta algún dato")

    def test_actionable_results_explain_the_no_javascript_fallback(self):
        self.client.force_login(self.professional)
        client_id = self.business.clients.get(full_name="Lucía Gómez").id

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "telefono",
                "services": self._combined_service_ids(),
                "target_date": "2026-07-09",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<noscript>", html=False)
        self.assertContains(response, "Estas horas corresponden a la última búsqueda.")
        self.assertContains(response, "pulsa «Buscar huecos» antes de elegir o confirmar.")

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

    def test_month_map_available_days_update_the_search_and_keep_its_context(self):
        self.client.force_login(self.professional)
        client_id = self.business.clients.get(full_name="Lucía Gómez").id
        service_ids = self._combined_service_ids()

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "whatsapp",
                "services": service_ids,
                "target_date": "2026-07-09",
                "selected_work_line_id": "2",
                "selected_starts_at": "2026-07-09T10:30:00+02:00",
            },
        )

        self.assertEqual(response.status_code, 200)
        available_card = next(
            card
            for card in response.context["month_days"]
            if card["day"].date > date(2026, 7, 9) and card["select_url"]
        )
        query = parse_qs(urlparse(available_card["select_url"]).query)

        self.assertEqual(query["business_client"], [str(client_id)])
        self.assertEqual(query["manual_channel"], ["whatsapp"])
        self.assertEqual(query["services"], [str(service_id) for service_id in service_ids])
        self.assertEqual(query["target_date"], [available_card["day"].date.isoformat()])
        self.assertEqual(query["requested_by_contact"], ["self"])
        self.assertNotIn("selected_work_line_id", query)
        self.assertNotIn("selected_starts_at", query)
        self.assertContains(response, "Pulsa un día en verde para actualizar la cita.")
        self.assertContains(response, 'aria-label="Mes anterior"')
        self.assertContains(response, 'aria-label="Mes siguiente"')
        self.assertContains(response, 'aria-current="date"')

    def test_month_map_navigation_can_browse_a_future_month_without_losing_search(self):
        self.client.force_login(self.professional)
        client_id = self.business.clients.get(full_name="Lucía Gómez").id
        service_ids = self._combined_service_ids()

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "telefono",
                "services": service_ids,
                "target_date": "2026-07-09",
                "calendar_year": "2026",
                "calendar_month": "8",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["calendar_month"], date(2026, 8, 1))
        self.assertEqual(len(response.context["month_leading_blanks"]), 5)
        self.assertEqual(len(response.context["month_trailing_blanks"]), 6)
        self.assertContains(response, '<h2 id="month-map-title">agosto 2026</h2>')
        self.assertContains(response, 'aria-label="Calendario de agosto 2026"')

        previous_query = parse_qs(
            urlparse(response.context["calendar_previous_url"]).query
        )
        next_query = parse_qs(urlparse(response.context["calendar_next_url"]).query)
        for query in (previous_query, next_query):
            self.assertEqual(query["business_client"], [str(client_id)])
            self.assertEqual(query["manual_channel"], ["telefono"])
            self.assertEqual(
                query["services"],
                [str(service_id) for service_id in service_ids],
            )
            self.assertEqual(query["target_date"], ["2026-07-09"])
        self.assertEqual(previous_query["calendar_year"], ["2026"])
        self.assertEqual(previous_query["calendar_month"], ["7"])
        self.assertEqual(next_query["calendar_year"], ["2026"])
        self.assertEqual(next_query["calendar_month"], ["9"])

    def test_month_map_ignores_invalid_navigation_parameters(self):
        self.client.force_login(self.professional)
        client_id = self.business.clients.get(full_name="Lucía Gómez").id

        response = self.client.get(
            reverse("booking:appointment_assistant"),
            {
                "business_client": client_id,
                "manual_channel": "telefono",
                "services": self._combined_service_ids(),
                "target_date": "2026-07-09",
                "calendar_year": "no-valido",
                "calendar_month": "13",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["calendar_month"], date(2026, 7, 1))
        self.assertContains(response, '<h2 id="month-map-title">julio 2026</h2>')

    def test_holiday_explains_why_the_selected_day_is_closed(self):
        self.client.force_login(self.professional)
        service_ids = self._combined_service_ids()
        client_id = self.business.clients.get(full_name="Lucía Gómez").id
        OfficialHoliday.objects.create(
            date=date(2026, 7, 10),
            name="Fiesta nacional",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="Fixture de prueba",
        )

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
        business_client = self.business.clients.get(full_name="Lucas López")
        requested_by = business_client.authorized_contacts.get(full_name="María López")
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
        self.assertContains(response, 'data-results-actionable="true"')
        quick_client_html = response.content.decode().split(
            'class="quick-client-card"',
            maxsplit=1,
        )[1].split("</form>", maxsplit=1)[0]
        self.assertIn(
            f'name="selected_work_line_id" value="{selected_slot.work_line_id}"',
            quick_client_html,
        )
        self.assertIn(
            f'name="selected_starts_at" value="{selected_slot.starts_at.isoformat()}"',
            quick_client_html,
        )

    def test_professional_can_confirm_recommended_slot(self):
        self.client.force_login(self.professional)
        service_ids = self._combined_service_ids()
        business_client = self.business.clients.get(full_name="Lucas López")
        requested_by = business_client.authorized_contacts.get(full_name="María López")
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
        self.assertEqual(appointment.requested_by_name_snapshot, "María López")
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
            "Te pediremos entrar o crear una cuenta únicamente cuando hayas elegido la hora",
        )
        self.assertNotContains(response, "Tu cuenta ya está iniciada")
        self.assertContains(
            response,
            f'href="{reverse("public_booking", args=[self.business.slug])}"',
        )
        self.assertNotContains(response, "Acceso profesional")
        self.assertContains(response, "Elegir esta hora")
        self.assertContains(response, "108,00 €")
        self.assertNotContains(response, "María López")
        self.assertNotContains(response, "600111201")
        self.assertNotContains(response, "Línea")
        self.assertContains(response, "/static/js/public_booking.js")
        self.assertNotContains(response, "<script>\n    (() => {")

    def test_public_service_list_uses_checkboxes_and_only_scrolls_above_five_services(self):
        response = self.client.get(reverse("public_booking", args=[self.business.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tú eliges antes de crear la cuenta")
        self.assertNotContains(response, "Tu cuenta ya está lista")
        self.assertContains(response, 'data-service-count="12"')
        self.assertContains(response, "service-choice-list--scrollable")
        self.assertContains(response, 'data-service-count="12" tabindex="0"')
        self.assertContains(
            response,
            "12 servicios disponibles. Desplázate para ver el catálogo completo.",
        )
        self.assertContains(
            response,
            'aria-describedby="public-services-scroll-hint"',
        )
        self.assertContains(response, 'type="checkbox"', count=12)

        services_to_pause = list(
            self.business.services.filter(is_active=True)
            .order_by("display_order", "pk")
            .values_list("pk", flat=True)[5:]
        )
        self.business.services.filter(pk__in=services_to_pause).update(is_active=False)

        response = self.client.get(reverse("public_booking", args=[self.business.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-service-count="5"')
        self.assertContains(response, 'type="checkbox"', count=5)
        self.assertNotContains(response, "service-choice-list--scrollable")
        self.assertNotContains(response, 'data-service-count="5" tabindex="0"')
        self.assertNotContains(response, "servicios disponibles. Desplázate")
        self.assertNotContains(response, 'id="public-services-scroll-hint"')

    def test_public_booking_reports_a_legacy_duration_incompatibility_without_500(self):
        self.business.calendar_settings.slot_interval_minutes = 30
        self.business.calendar_settings.save(update_fields=["slot_interval_minutes"])
        incompatible_service = self.business.services.get(name="Color completo")
        self.assertEqual(incompatible_service.duration_minutes, 90)
        incompatible_service.duration_minutes = 45
        incompatible_service.save(update_fields=["duration_minutes", "updated_at"])

        response = self.client.get(
            reverse("public_booking", args=[self.business.slug]),
            {
                "services": [incompatible_service.pk],
                "target_date": self._target_date().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no es compatible con el intervalo de agenda de 30 minutos")

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
        self.assertEqual(str(UUID(draft["confirmation_reference"])), draft["confirmation_reference"])
        self.assertEqual(set(draft["service_ids"]), set(service_ids))
        self.assertEqual(draft["selected_work_line_id"], slot.work_line_id)
        self.assertFalse(Appointment.objects.filter(starts_at=slot.starts_at, manual_channel=Appointment.ManualChannel.PUBLIC_WEB).exists())
        access_response = self.client.get(response["Location"])
        self.assertContains(access_response, "Tu hora sigue")
        self.assertContains(access_response, "Entrar y revisar reserva")

    def test_public_confirmation_replay_returns_the_same_receipt_without_duplicates(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        stored_draft = dict(
            self.client.session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY][str(self.business.id)]
        )
        self._login_demo_client(
            next_url=(
                f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
            )
        )

        first_response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking"},
        )
        self.assertEqual(first_response.status_code, 302)
        appointment = Appointment.objects.get(
            business=self.business,
            business_client__full_name="María López",
            starts_at=slot.starts_at,
            manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
        )
        self.assertEqual(
            str(appointment.public_confirmation_reference),
            stored_draft["confirmation_reference"],
        )
        initial_email_ids = tuple(
            appointment.outbound_emails.order_by("pk").values_list("pk", flat=True)
        )
        self.assertTrue(initial_email_ids)

        session = self.client.session
        session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY] = {
            str(self.business.id): stored_draft,
        }
        session.save()
        replay_response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking"},
        )

        self.assertEqual(replay_response.status_code, 302)
        self.assertEqual(replay_response["Location"], first_response["Location"])
        self.assertEqual(
            Appointment.objects.filter(
                public_confirmation_reference=stored_draft["confirmation_reference"],
            ).count(),
            1,
        )
        self.assertEqual(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=BusinessActivityEvent.EventType.APPOINTMENT_CREATED,
                entity_type="appointment",
                entity_id=str(appointment.pk),
            ).count(),
            1,
        )
        self.assertEqual(
            tuple(
                appointment.outbound_emails.order_by("pk").values_list(
                    "pk", flat=True
                )
            ),
            initial_email_ids,
        )
        self.assertEqual(
            self.client.session[PUBLIC_BOOKING_RECEIPTS_SESSION_KEY][
                str(self.business.id)
            ]["appointment_id"],
            appointment.pk,
        )

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=True)
    def test_public_receipt_keeps_normal_email_copy_when_delivery_is_enabled(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = (
            f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        )
        self._login_demo_client(next_url=confirmation_url)
        confirmed = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking"},
        )

        receipt = self.client.get(confirmed["Location"])

        self.assertEqual(receipt.status_code, 200)
        self.assertContains(receipt, "Tu cita está confirmada")
        self.assertContains(receipt, "Confirmación por correo")
        self.assertNotContains(receipt, "Entrega externa desactivada")
        self.assertNotContains(receipt, "no envía correos externos")

    def test_authenticated_public_search_protects_personal_account_data(self):
        self._login_demo_client()

        response = self.client.get(
            reverse("public_booking", args=[self.business.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertContains(response, "María López")
        self.assertContains(response, "600 111 201")
        self.assertContains(response, "Tu cuenta ya está iniciada")
        self.assertContains(response, "Ya estás dentro de tu cuenta")
        self.assertContains(response, "Tu cuenta ya está lista")
        self.assertContains(response, "Revisa y confirma")
        self.assertNotContains(response, "Consulta las horas disponibles sin registrarte")
        self.assertNotContains(response, "Tú eliges antes de crear la cuenta")

    @override_settings(AGENDA_TRANSACTIONAL_EMAIL_ENABLED=False)
    def test_login_resumes_review_and_final_confirmation_uses_same_engine(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"

        login_response = self._login_demo_client(next_url=confirmation_url)
        self.assertEqual(login_response["Location"], confirmation_url)
        review_response = self.client.get(confirmation_url)

        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_response["Cache-Control"], "no-store")
        self.assertEqual(review_response["Referrer-Policy"], "same-origin")
        self.assertContains(review_response, "Revisa y confirma")
        self.assertContains(review_response, "María López")
        self.assertContains(review_response, "108,00 €")
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
        self.assertEqual(receipt_response["Cache-Control"], "no-store")
        self.assertEqual(receipt_response["Referrer-Policy"], "same-origin")
        self.assertContains(receipt_response, "Tu cita está confirmada")
        self.assertContains(receipt_response, "María López")
        self.assertContains(receipt_response, "108,00 €")
        self.assertContains(receipt_response, "Estado de la cita")
        self.assertNotContains(receipt_response, "Confirmación por correo")
        self.assertContains(receipt_response, "Entrega externa desactivada")
        self.assertContains(receipt_response, "La cita ya está confirmada en AgendaSalon")
        self.assertNotContains(receipt_response, "próximo intento")

        refreshed_response = self.client.get(response["Location"])
        self.assertEqual(refreshed_response.status_code, 200)
        self.assertContains(refreshed_response, "Tu cita está confirmada")

    def test_public_confirmation_accepts_a_real_same_origin_csrf_submission(self):
        browser = Client(enforce_csrf_checks=True)
        booking_url = reverse("public_booking", args=[self.business.slug])
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()

        browser.get(
            booking_url,
            {
                "services": service_ids,
                "target_date": self._target_date().isoformat(),
            },
            secure=True,
        )
        csrf_token = browser.cookies["csrftoken"].value
        choose_response = browser.post(
            booking_url,
            {
                "csrfmiddlewaretoken": csrf_token,
                "action": "choose_slot",
                "services": service_ids,
                "target_date": self._target_date().isoformat(),
                "selected_work_line_id": slot.work_line_id,
                "selected_starts_at": slot.starts_at.isoformat(),
            },
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        self.assertEqual(choose_response.status_code, 302)

        confirmation_url = f"{booking_url}?confirm=1"
        login_url = reverse("customers:client_access", args=[self.business.slug])
        browser.get(
            f"{login_url}?{urlencode({'next': confirmation_url})}",
            secure=True,
        )
        csrf_token = browser.cookies["csrftoken"].value
        login_response = browser.post(
            login_url,
            {
                "csrfmiddlewaretoken": csrf_token,
                "next": confirmation_url,
                "phone": "600111201",
                "password": "AgendaSalonDemo2",
            },
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertEqual(login_response["Location"], confirmation_url)

        review_response = browser.get(confirmation_url, secure=True)
        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_response["Referrer-Policy"], "same-origin")
        csrf_token = browser.cookies["csrftoken"].value

        confirm_response = browser.post(
            confirmation_url,
            {
                "csrfmiddlewaretoken": csrf_token,
                "action": "confirm_booking",
            },
            HTTP_ORIGIN="https://testserver",
            secure=True,
        )

        self.assertEqual(confirm_response.status_code, 302)
        self.assertEqual(
            confirm_response["Location"],
            reverse("public_booking_receipt", args=[self.business.slug]),
        )
        self.assertTrue(
            Appointment.objects.filter(
                business=self.business,
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
                status=Appointment.Status.CONFIRMED,
            ).exists()
        )

    def test_public_confirmation_requires_the_current_privacy_version(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        self._login_demo_client(next_url=confirmation_url)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="María López",
        )
        previous_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        previous_document.is_active = False
        previous_document.save(update_fields=["is_active"])
        current_document = LegalDocument.objects.create(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            slug="privacidad-clientes-v2-prueba",
            version="test-v2",
            title="Privacidad de clientes",
            lead="Información actualizada para la prueba.",
            sections=[{"heading": "Responsable", "body": "Información vigente."}],
            is_active=True,
        )

        review_response = self.client.get(confirmation_url)

        self.assertEqual(review_response.status_code, 200)
        self.assertContains(review_response, "información vigente sobre el tratamiento")
        self.assertContains(review_response, "versión test-v2")

        rejected_response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking"},
        )

        self.assertEqual(rejected_response.status_code, 200)
        self.assertContains(rejected_response, "marca la casilla de lectura")
        self.assertIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, self.client.session)
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

        confirmed_response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {
                "action": "confirm_booking",
                "privacy_acknowledged": "on",
                "legal_presentation_token": review_response.context[
                    "legal_presentation_token"
                ],
            },
        )

        self.assertEqual(confirmed_response.status_code, 302)
        self.assertTrue(
            CustomerPrivacyEvidence.objects.filter(
                business=self.business,
                business_client=access.business_client,
                client_access=access,
                document=current_document,
                channel=CustomerPrivacyEvidence.Channel.BOOKING,
            ).exists()
        )
        privacy_event = CustomerPrivacyEvidenceEvent.objects.get(
            business=self.business,
            business_client=access.business_client,
            client_access=access,
            document=current_document,
            channel=CustomerPrivacyEvidence.Channel.BOOKING,
        )
        acceptance_event = LegalAcceptanceEvent.objects.get(
            business=self.business,
            client_access=access,
            document=current_document,
        )
        self.assertTrue(privacy_event.action_fingerprint)
        self.assertTrue(acceptance_event.action_fingerprint)

    def test_public_confirmation_rejects_a_policy_changed_after_review(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        self._login_demo_client(next_url=confirmation_url)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="María López",
        )
        old_document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        CustomerPrivacyEvidence.objects.filter(
            business=self.business,
            business_client=access.business_client,
            document=old_document,
        ).delete()

        review_response = self.client.get(confirmation_url)
        old_token = review_response.context["legal_presentation_token"]
        self.assertTrue(old_token)

        old_document.is_active = False
        old_document.save(update_fields=["is_active"])
        new_document = LegalDocument.objects.create(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            slug="privacidad-clientes-cambio-durante-reserva",
            version="test-cambio-formulario",
            title="Privacidad de clientes",
            lead="Información actualizada durante la reserva.",
            sections=[{"heading": "Responsable", "body": "Información vigente."}],
            is_active=True,
        )

        rejected_response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {
                "action": "confirm_booking",
                "privacy_acknowledged": "on",
                "legal_presentation_token": old_token,
            },
        )

        self.assertEqual(rejected_response.status_code, 200)
        self.assertContains(
            rejected_response,
            LEGAL_PRESENTATION_CHANGED_MESSAGE,
        )
        self.assertContains(rejected_response, "versión test-cambio-formulario")
        self.assertIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, self.client.session)
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )
        self.assertFalse(
            CustomerPrivacyEvidence.objects.filter(
                business=self.business,
                business_client=access.business_client,
                document=new_document,
            ).exists()
        )

        confirmed_response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {
                "action": "confirm_booking",
                "privacy_acknowledged": "on",
                "legal_presentation_token": rejected_response.context[
                    "legal_presentation_token"
                ],
            },
        )

        self.assertEqual(confirmed_response.status_code, 302)
        self.assertTrue(
            CustomerPrivacyEvidence.objects.filter(
                business=self.business,
                business_client=access.business_client,
                document=new_document,
                channel=CustomerPrivacyEvidence.Channel.BOOKING,
            ).exists()
        )

    def test_public_confirmation_pauses_cleanly_without_a_privacy_document(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = (
            f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        )
        self._login_demo_client(next_url=confirmation_url)
        document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )
        document.is_active = False
        document.save(update_fields=["is_active"])
        evidence_count = CustomerPrivacyEvidence.objects.count()

        page = self.client.get(confirmation_url)
        response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking"},
        )

        for result in (page, response):
            with self.subTest(method=result.request["REQUEST_METHOD"]):
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result["Cache-Control"], "no-store")
                self.assertEqual(result["Referrer-Policy"], "same-origin")
                self.assertContains(
                    result,
                    f"public-booking-body--{result.context['client_auth_theme']}",
                    status_code=503,
                )
                self.assertContains(
                    result,
                    self.business.commercial_name,
                    status_code=503,
                )
                self.assertContains(
                    result,
                    "Reserva tu cita en este salón",
                    status_code=503,
                )
                self.assertContains(
                    result,
                    f"Hola, {result.context['client_access'].business_client.full_name}",
                    status_code=503,
                )
                self.assertNotContains(
                    result,
                    reverse("accounts:login"),
                    status_code=503,
                )
                self.assertContains(
                    result,
                    "No podemos confirmar la reserva ahora",
                    status_code=503,
                )
                self.assertContains(
                    result,
                    "No hemos creado la cita ni guardado datos nuevos",
                    status_code=503,
                )
                self.assertContains(
                    result,
                    "?confirm=1",
                    status_code=503,
                )
                self.assertContains(
                    result,
                    "Reintentar confirmación",
                    status_code=503,
                )
                self.assertContains(
                    result,
                    "aunque cambies el servicio o la hora",
                    status_code=503,
                )
                self.assertContains(
                    result,
                    f'href="tel:{self.business.public_phone}"',
                    status_code=503,
                )
                self.assertContains(
                    result,
                    f'href="mailto:{self.business.public_email}"',
                    status_code=503,
                )
                self.assertContains(
                    result,
                    "Volver a servicios y horas",
                    status_code=503,
                )
        self.assertIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, self.client.session)
        self.assertEqual(CustomerPrivacyEvidence.objects.count(), evidence_count)
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

    def test_public_confirmation_rechecks_document_availability_inside_lock(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = (
            f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        )
        self._login_demo_client(next_url=confirmation_url)
        document = LegalDocument.objects.get(
            kind=LegalDocument.Kind.CUSTOMER_PRIVACY,
            is_active=True,
        )

        def withdraw_document_after_calendar_lock(business):
            locked_calendar = real_lock_business_calendar(business)
            LegalDocument.objects.filter(pk=document.pk).update(is_active=False)
            return locked_calendar

        with patch(
            "apps.booking.views.lock_business_calendar",
            side_effect=withdraw_document_after_calendar_lock,
        ):
            response = self.client.post(
                reverse("public_booking", args=[self.business.slug]),
                {"action": "confirm_booking"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, self.client.session)
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )
        document.refresh_from_db()
        self.assertTrue(document.is_active)

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

        legacy_draft_without_reference = {
            "service_ids": [1],
            "target_date": now.date().isoformat(),
            "selected_work_line_id": 1,
            "selected_starts_at": now.isoformat(),
            "saved_at": now.isoformat(),
        }
        request.session = {
            PUBLIC_BOOKING_DRAFTS_SESSION_KEY: {
                business_key: legacy_draft_without_reference,
            }
        }
        self.assertIsNone(get_public_booking_draft(request, self.business))
        self.assertNotIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, request.session)

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

    def test_online_account_cannot_book_for_family_profile_with_stale_privacy(self):
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
        CustomerPrivacyEvidence.objects.filter(
            business=self.business,
            business_client=beneficiary,
        ).delete()
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = (
            f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        )
        self._login_demo_client(next_url=confirmation_url)

        response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking", "business_client": beneficiary.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "pide al salón que actualice su información de privacidad",
        )
        self.assertEqual(response.context["selected_business_client"], beneficiary)
        self.assertIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, self.client.session)
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                business_client=beneficiary,
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

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

    def test_public_confirmation_revalidates_the_account_inside_the_booking_lock(self):
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="María López",
        )
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = (
            f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        )
        self._login_demo_client(next_url=confirmation_url)

        def deactivate_access_before_lock(business):
            BusinessClientAccess.objects.filter(pk=access.pk).update(is_active=False)
            return real_lock_business_calendar(business)

        with patch(
            "apps.booking.views.lock_business_calendar",
            side_effect=deactivate_access_before_lock,
        ):
            response = self.client.post(
                reverse("public_booking", args=[self.business.slug]),
                {"action": "confirm_booking"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                business_client=access.business_client,
                starts_at=slot.starts_at,
                manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
            ).exists()
        )

    def test_public_confirmation_revalidates_password_fingerprint_inside_lock(self):
        access = BusinessClientAccess.objects.get(
            business=self.business,
            business_client__full_name="María López",
        )
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        confirmation_url = (
            f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        )
        self._login_demo_client(next_url=confirmation_url)

        def change_password_before_lock(business):
            changed_access = BusinessClientAccess.objects.get(pk=access.pk)
            changed_access.set_password("NuevaClaveSeguraDePrueba2026!")
            changed_access.save(update_fields=["password_hash", "updated_at"])
            return real_lock_business_calendar(business)

        with patch(
            "apps.booking.views.lock_business_calendar",
            side_effect=change_password_before_lock,
        ):
            response = self.client.post(
                reverse("public_booking", args=[self.business.slug]),
                {"action": "confirm_booking"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Appointment.objects.filter(
                business=self.business,
                business_client=access.business_client,
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

    def test_legacy_public_draft_without_reference_requires_choosing_again(self):
        self._login_demo_client()
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        self._choose_public_slot(slot, service_ids)
        session = self.client.session
        drafts = session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY]
        drafts[str(self.business.id)].pop("confirmation_reference")
        session[PUBLIC_BOOKING_DRAFTS_SESSION_KEY] = drafts
        session.save()

        response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La selección ha caducado")
        self.assertContains(response, "Elige de nuevo los servicios y la hora")
        self.assertNotIn(PUBLIC_BOOKING_DRAFTS_SESSION_KEY, self.client.session)
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
        same_time_slots = [
            candidate
            for candidate in get_day_availability(
                business=self.business,
                target_date=slot.starts_at.date(),
                duration_minutes=180,
            ).slots
            if candidate.starts_at == slot.starts_at
        ]
        self.assertTrue(same_time_slots)
        for candidate in same_time_slots:
            Appointment.objects.create(
                business=self.business,
                business_client=self.business.clients.get(full_name="Lucía Gómez"),
                work_line_id=candidate.work_line_id,
                starts_at=candidate.starts_at,
                ends_at=candidate.starts_at + timedelta(minutes=180),
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

    def test_public_confirmation_keeps_the_time_when_another_internal_line_is_free(self):
        service_ids = self._combined_service_ids()
        slot = self._first_public_slot()
        same_time_slots = [
            candidate
            for candidate in get_day_availability(
                business=self.business,
                target_date=slot.starts_at.date(),
                duration_minutes=180,
            ).slots
            if candidate.starts_at == slot.starts_at
        ]
        self.assertGreaterEqual(len(same_time_slots), 2)
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
            service_summary_snapshot="Bloqueo de una línea",
        )
        confirmation_url = f"{reverse('public_booking', args=[self.business.slug])}?confirm=1"
        self._login_demo_client(next_url=confirmation_url)

        review_response = self.client.get(confirmation_url)
        response = self.client.post(
            reverse("public_booking", args=[self.business.slug]),
            {"action": "confirm_booking"},
        )

        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(response.status_code, 302)
        appointment = Appointment.objects.get(
            business=self.business,
            business_client__full_name="María López",
            starts_at=slot.starts_at,
            manual_channel=Appointment.ManualChannel.PUBLIC_WEB,
        )
        self.assertNotEqual(appointment.work_line_id, slot.work_line_id)

    def _combined_service_ids(self):
        return list(
            Service.objects.filter(
                business=self.business,
                name__in=[
                    "Lavado y preparación",
                    "Color completo",
                    "Corte mujer",
                    "Peinado cabello largo",
                ],
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
                "password": "AgendaSalonDemo2",
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

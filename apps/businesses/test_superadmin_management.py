from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.booking.models import BusinessCalendarSettings
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership


class SuperadminBusinessManagementTests(TestCase):
    def setUp(self):
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000001",
            phone="+34910000001",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600111001",
            phone="+34600111001",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
            public_booking_enabled=True,
        )
        self.membership = BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
        )

    def test_business_list_requires_superadmin_and_does_not_open_booking_flow(self):
        list_url = reverse("businesses:superadmin_business_list")
        self.assertEqual(self.client.get(list_url).status_code, 302)

        self.client.force_login(self.professional)
        self.assertEqual(self.client.get(list_url).status_code, 403)

        self.client.force_login(self.superadmin)
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nuevo negocio")
        self.assertContains(response, "Gestionar")
        self.assertNotContains(response, "Abrir reserva")

    def test_business_form_uses_correct_accents_and_business_field_structure(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(
            reverse("businesses:superadmin_business_edit", args=[self.business.id])
        )

        self.assertEqual(response.status_code, 200)
        for label in (
            "Nombre comercial",
            "Identificador público",
            "Teléfono público",
            "Correo público",
            "Dirección",
            "Descripción pública",
            "Reserva pública activa",
        ):
            self.assertContains(response, label)
        self.assertContains(response, "field--business-description")
        self.assertContains(response, "field field--wide")
        self.assertContains(response, 'type="tel"')
        form = response.context["business_form"]
        self.assertEqual(form.fields["slug"].help_text, "")
        self.assertEqual(
            form.fields["slug"].widget.attrs["placeholder"],
            "Se genera desde el nombre si lo dejas vacío",
        )

    def test_first_professional_fields_keep_help_outside_the_grid(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(reverse("businesses:superadmin_business_create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="professional-access-guidance"')
        self.assertContains(
            response,
            "Será su identificador para entrar en AgendaSalon.",
            count=1,
        )
        self.assertContains(
            response,
            "Debe tener al menos 8 caracteres y no ser demasiado común.",
            count=1,
        )
        self.assertContains(response, "Correo electrónico (opcional)")
        professional_form = response.context["professional_form"]
        for field_name in ("phone", "password"):
            self.assertEqual(
                professional_form.fields[field_name].widget.attrs["aria-describedby"],
                "professional-access-guidance",
            )

    def test_superadmin_creates_business_with_first_professional_atomically(self):
        self.client.force_login(self.superadmin)
        response = self.client.post(
            reverse("businesses:superadmin_business_create"),
            {
                "commercial_name": "Salón Centro",
                "slug": "",
                "public_description": "Peluquería de barrio.",
                "public_phone": "600333001",
                "public_email": "hola@saloncentro.local",
                "address": "Calle Centro 8",
                "city": "Madrid",
                "province": "Madrid",
                "is_active": "on",
                "full_name": "Laura Profesional",
                "phone": "600333002",
                "email": "laura@saloncentro.local",
                "password": "AgendaSalonNueva2026!",
            },
        )

        business = Business.objects.get(slug="salon-centro")
        professional = get_user_model().objects.get(normalized_phone="+34600333002")
        self.assertRedirects(
            response,
            reverse("businesses:superadmin_business_detail", args=[business.id]),
        )
        self.assertTrue(business.is_active)
        self.assertFalse(business.public_booking_enabled)
        self.assertTrue(professional.check_password("AgendaSalonNueva2026!"))
        self.assertTrue(
            BusinessMembership.objects.filter(
                business=business,
                user=professional,
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=business,
                event_type=BusinessActivityEvent.EventType.BUSINESS_CREATED,
                actor_user=self.superadmin,
            ).exists()
        )
        self.assertTrue(
            BusinessCalendarSettings.objects.filter(
                business=business,
                apply_national_holidays=True,
            ).exists()
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=business,
                event_type=BusinessActivityEvent.EventType.MEMBERSHIP_CREATED,
            ).exists()
        )

    def test_business_edit_records_changed_fields_without_exposing_values(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(
            reverse("businesses:superadmin_business_edit", args=[self.business.id]),
            {
                "commercial_name": "Peluquería Mari Centro",
                "slug": self.business.slug,
                "public_phone": "600111001",
                "public_email": "hola@mari.local",
                "address": "Calle Mayor 12",
                "city": "Madrid",
                "province": "Madrid",
                "public_description": "Salón de belleza.",
                "is_active": "on",
                "public_booking_enabled": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        event = BusinessActivityEvent.objects.get(
            business=self.business,
            event_type=BusinessActivityEvent.EventType.BUSINESS_UPDATED,
        )
        self.assertIn("Nombre comercial", event.summary)
        self.assertIn("Teléfono público", event.summary)
        self.assertNotIn("600111001", event.summary)
        self.assertIn("commercial_name", event.changes["fields"])

    def test_duplicate_professional_phone_does_not_create_partial_business(self):
        self.client.force_login(self.superadmin)
        response = self.client.post(
            reverse("businesses:superadmin_business_create"),
            {
                "commercial_name": "Salón Duplicado",
                "slug": "salon-duplicado",
                "is_active": "on",
                "full_name": "Otra profesional",
                "phone": self.professional.normalized_phone,
                "password": "AgendaSalonNueva2026!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ya existe una cuenta interna con este teléfono")
        self.assertFalse(Business.objects.filter(slug="salon-duplicado").exists())

    def test_superadmin_can_pause_business_without_deleting_history(self):
        self.client.force_login(self.superadmin)
        response = self.client.post(
            reverse("businesses:superadmin_business_toggle", args=[self.business.id])
        )

        self.assertEqual(response.status_code, 302)
        self.business.refresh_from_db()
        self.assertFalse(self.business.is_active)
        self.assertTrue(BusinessMembership.objects.filter(pk=self.membership.id).exists())
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=BusinessActivityEvent.EventType.BUSINESS_PAUSED,
            ).exists()
        )

    def test_public_booking_toggle_disables_business_public_routes(self):
        self.client.force_login(self.superadmin)
        response = self.client.post(
            reverse("businesses:superadmin_public_booking_toggle", args=[self.business.id])
        )

        self.assertEqual(response.status_code, 302)
        self.business.refresh_from_db()
        self.assertFalse(self.business.public_booking_enabled)

        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("public_booking", args=[self.business.slug])).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(reverse("customers:client_access", args=[self.business.slug])).status_code,
            404,
        )

    def test_superadmin_can_pause_and_reactivate_professional_membership(self):
        self.client.force_login(self.superadmin)
        toggle_url = reverse(
            "businesses:superadmin_membership_toggle",
            args=[self.business.id, self.membership.id],
        )

        self.client.post(toggle_url)
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active)

        self.client.post(toggle_url)
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)
        self.assertEqual(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                category=BusinessActivityEvent.Category.ACCESS,
            ).count(),
            2,
        )

    def test_business_detail_activity_is_filtered_and_isolated(self):
        other_business = Business.objects.create(
            commercial_name="Barbería Norte",
            slug="barberia-norte",
        )
        BusinessActivityEvent.objects.create(
            business=self.business,
            actor_type=BusinessActivityEvent.ActorType.PROFESSIONAL,
            actor_label="Mari Profesional",
            category=BusinessActivityEvent.Category.CONFIGURATION,
            event_type=BusinessActivityEvent.EventType.SERVICE_UPDATED,
            origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
            summary='Servicio "Tinte" actualizado.',
        )
        BusinessActivityEvent.objects.create(
            business=other_business,
            actor_type=BusinessActivityEvent.ActorType.PROFESSIONAL,
            actor_label="Norte Profesional",
            category=BusinessActivityEvent.Category.CONFIGURATION,
            event_type=BusinessActivityEvent.EventType.SERVICE_UPDATED,
            origin=BusinessActivityEvent.Origin.PROFESSIONAL_PANEL,
            summary='Servicio "Degradado" actualizado.',
        )
        self.client.force_login(self.superadmin)

        response = self.client.get(
            reverse("businesses:superadmin_business_detail", args=[self.business.id]),
            {"activity": BusinessActivityEvent.Category.CONFIGURATION},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Actividad reciente")
        self.assertContains(response, 'Servicio &quot;Tinte&quot; actualizado.')
        self.assertNotContains(response, "Degradado")
        self.assertContains(response, "Creadas por el equipo")
        self.assertContains(response, "Reservas online")

    def test_business_detail_limits_recent_activity_to_six_events(self):
        for index in range(8):
            BusinessActivityEvent.objects.create(
                business=self.business,
                actor_type=BusinessActivityEvent.ActorType.SYSTEM,
                actor_label="Sistema",
                category=BusinessActivityEvent.Category.PLATFORM,
                event_type=BusinessActivityEvent.EventType.BUSINESS_UPDATED,
                origin=BusinessActivityEvent.Origin.SYSTEM,
                summary=f"Movimiento reciente {index + 1}.",
            )
        self.client.force_login(self.superadmin)

        response = self.client.get(
            reverse("businesses:superadmin_business_detail", args=[self.business.id])
        )

        self.assertEqual(len(response.context["activity_events"]), 6)
        self.assertContains(response, "Ver historial completo · 8 movimientos")
        self.assertNotContains(response, "Consultar 8 movimientos")

    def test_full_activity_history_uses_numbered_pages_and_requires_superadmin(self):
        history_url = reverse(
            "businesses:superadmin_business_activity",
            args=[self.business.id],
        )
        self.assertEqual(self.client.get(history_url).status_code, 302)

        self.client.force_login(self.professional)
        self.assertEqual(self.client.get(history_url).status_code, 403)

        for index in range(31):
            BusinessActivityEvent.objects.create(
                business=self.business,
                actor_type=BusinessActivityEvent.ActorType.SYSTEM,
                actor_label="Sistema",
                category=BusinessActivityEvent.Category.PLATFORM,
                event_type=BusinessActivityEvent.EventType.BUSINESS_UPDATED,
                origin=BusinessActivityEvent.Origin.SYSTEM,
                summary=f"Movimiento {index + 1}.",
            )

        self.client.force_login(self.superadmin)
        response = self.client.get(history_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["activity_events"]), 10)
        self.assertEqual(response.context["activity_page"].paginator.num_pages, 4)
        self.assertContains(response, "Página 1 de 4")
        self.assertContains(response, "?activity=all&amp;page=2")
        self.assertNotContains(response, "Mostrar movimientos anteriores")

        second_page = self.client.get(history_url, {"activity": "all", "page": 2})

        self.assertEqual(len(second_page.context["activity_events"]), 10)
        self.assertContains(second_page, "Página 2 de 4")
        self.assertContains(second_page, "?activity=all&amp;page=1")

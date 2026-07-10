from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.models import Appointment
from apps.businesses.models import Business
from apps.customers.models import BusinessClient, BusinessClientAccess, BusinessClientAuthorizedContact
from apps.customers.services import register_client_access


class CustomerModelTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )

    def test_client_normalizes_name_and_phone(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="  Maria   Lopez  ",
            phone="600 111 222",
        )

        self.assertEqual(client.full_name_normalized, "maria lopez")
        self.assertEqual(client.phone_normalized, "+34600111222")

    def test_active_client_identity_is_unique_inside_business(self):
        BusinessClient.objects.create(
            business=self.business,
            full_name="Maria Lopez",
            phone="600111222",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            BusinessClient.objects.create(
                business=self.business,
                full_name="Maria   Lopez",
                phone="+34 600 111 222",
            )

    def test_authorized_contact_must_belong_to_same_business(self):
        other_business = Business.objects.create(
            commercial_name="Salon Norte",
            slug="salon-norte",
        )
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="Lucia Gomez",
            phone="600111333",
        )

        contact = BusinessClientAuthorizedContact(
            business=other_business,
            business_client=client,
            full_name="Ana Gomez",
            phone="600111444",
        )

        with self.assertRaises(ValidationError):
            contact.full_clean()

    def test_only_one_active_primary_contact_per_client(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="Lucia Gomez",
            phone="600111333",
        )
        BusinessClientAuthorizedContact.objects.create(
            business=self.business,
            business_client=client,
            full_name="Ana Gomez",
            phone="600111444",
            is_primary_contact=True,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            BusinessClientAuthorizedContact.objects.create(
                business=self.business,
                business_client=client,
                full_name="Carlos Gomez",
                phone="600111555",
                is_primary_contact=True,
            )

    def test_client_access_reuses_existing_client_file(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="Maria Lopez",
            phone="600111222",
        )

        access = register_client_access(
            business=self.business,
            full_name="Maria Lopez",
            phone="600111222",
            password="ClienteDemo2026!",
        )

        self.assertEqual(access.business_client, client)
        self.assertTrue(access.check_password("ClienteDemo2026!"))

    def test_client_access_phone_is_unique_inside_business(self):
        client = BusinessClient.objects.create(
            business=self.business,
            full_name="Maria Lopez",
            phone="600111222",
        )
        access = BusinessClientAccess(
            business=self.business,
            business_client=client,
            phone="600111222",
        )
        access.set_password("ClienteDemo2026!")
        access.save()

        other_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Maria L.",
            phone="+34 600 111 222",
        )
        duplicate = BusinessClientAccess(
            business=self.business,
            business_client=other_client,
            phone="+34 600 111 222",
        )
        duplicate.set_password("ClienteDemo2026!")

        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()


class ClientAccessViewTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )

    def test_client_access_page_uses_customer_copy(self):
        response = self.client.get(
            reverse("customers:client_access", args=[self.business.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Peluquería Mari")
        self.assertContains(response, "Zona cliente de Peluquería Mari")
        self.assertContains(response, "Reserva en")
        self.assertContains(response, "Entrar para reservar")
        self.assertContains(response, "Entrar en mi cuenta")
        self.assertContains(response, "Créala en un momento")
        self.assertContains(response, "client-auth-content")
        self.assertContains(response, "client-auth-image-space")
        self.assertContains(response, "client-auth-page--salon")
        self.assertNotContains(response, 'name="full_name"')
        self.assertNotContains(response, "Crear cuenta y revisar reserva")
        self.assertNotContains(response, "Acceso privado para cuentas registradas.")
        self.assertNotContains(response, "Entrar en AgendaSalon")

    def test_client_register_page_uses_separate_registration_flow(self):
        response = self.client.get(
            reverse("customers:client_register", args=[self.business.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cuenta cliente de Peluquería Mari")
        self.assertContains(response, "Crea tu cuenta")
        self.assertContains(response, "en Peluquería Mari")
        self.assertContains(response, "Crear cuenta cliente")
        self.assertContains(response, "Crear mi cuenta")
        self.assertContains(response, "Entra para reservar")
        self.assertContains(response, "client-auth-register-page")
        self.assertContains(response, "client-auth-page--salon")
        self.assertNotContains(response, "Entrar y revisar reserva")

    def test_barberia_business_uses_masculine_visual_theme(self):
        barberia = Business.objects.create(
            commercial_name="Barbería Norte",
            slug="barberia-norte",
            is_active=True,
        )

        response = self.client.get(
            reverse("customers:client_access", args=[barberia.slug])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Zona cliente de Barbería Norte")
        self.assertContains(response, "client-auth-page--barberia")

    def test_client_login_is_scoped_to_business_slug(self):
        other_business = Business.objects.create(
            commercial_name="Salon Norte",
            slug="salon-norte",
            is_active=True,
        )
        other_client = BusinessClient.objects.create(
            business=other_business,
            full_name="Cliente Norte",
            phone="600999222",
        )
        other_access = BusinessClientAccess(
            business=other_business,
            business_client=other_client,
            phone="600999222",
        )
        other_access.set_password("ClienteDemo2026!")
        other_access.save()

        response = self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {
                "next": reverse("public_booking", args=[self.business.slug]),
                "phone": "600999222",
                "password": "ClienteDemo2026!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Teléfono o contraseña no válidos.")

        response = self.client.post(
            reverse("customers:client_access", args=[other_business.slug]),
            {
                "next": reverse("public_booking", args=[other_business.slug]),
                "phone": "600999222",
                "password": "ClienteDemo2026!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("public_booking", args=[other_business.slug]))

    def test_registration_logs_client_into_booking_flow(self):
        response = self.client.post(
            reverse("customers:client_register", args=[self.business.slug]),
            {
                "next": reverse("public_booking", args=[self.business.slug]),
                "full_name": "Cliente Web",
                "phone": "600999001",
                "password": "ClienteDemo2026!",
                "password_confirm": "ClienteDemo2026!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("public_booking", args=[self.business.slug]))
        self.assertTrue(
            BusinessClientAccess.objects.filter(
                business=self.business,
                business_client__full_name="Cliente Web",
                phone_normalized="+34600999001",
            ).exists()
        )

    def test_client_logout_requires_post_and_clears_the_business_session(self):
        register_client_access(
            business=self.business,
            full_name="Cliente Web",
            phone="600999001",
            password="ClienteDemo2026!",
        )
        self.client.post(
            reverse("customers:client_access", args=[self.business.slug]),
            {
                "phone": "600999001",
                "password": "ClienteDemo2026!",
            },
        )
        logout_url = reverse("customers:client_logout", args=[self.business.slug])

        self.assertEqual(self.client.get(logout_url).status_code, 405)
        response = self.client.post(logout_url)

        self.assertRedirects(
            response,
            reverse("customers:client_access", args=[self.business.slug]),
        )
        booking_response = self.client.get(reverse("public_booking", args=[self.business.slug]))
        self.assertEqual(booking_response.status_code, 200)
        self.assertContains(booking_response, "No necesitas una cuenta para consultar servicios y horas")
        self.assertNotContains(booking_response, "Reservas como")


class ProfessionalClientViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", base_date="2026-07-06", stdout=StringIO())
        cls.business = Business.objects.get(slug="peluqueria-mari")
        cls.other_business = Business.objects.get(slug="barberia-norte")
        cls.professional = get_user_model().objects.get(normalized_phone="+34600111001")

    def test_professional_client_list_requires_login(self):
        response = self.client.get(reverse("customers:professional_client_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_professional_client_list_shows_business_clients(self):
        self.client.force_login(self.professional)

        response = self.client.get(reverse("customers:professional_client_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Clientes de Peluquería Mari")
        self.assertContains(response, "Maria Lopez")
        self.assertContains(response, "Guardar cliente")
        self.assertNotContains(response, "Javier Martin")

    def test_professional_can_create_client_from_client_list(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("customers:professional_client_list"),
            {
                "full_name": "Paula Vega",
                "phone": "600333111",
                "email": "paula@example.local",
                "internal_notes": "Prefiere primera hora.",
            },
        )

        client = BusinessClient.objects.get(business=self.business, full_name="Paula Vega")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("customers:professional_client_detail", args=[client.id]))
        self.assertEqual(client.phone_normalized, "+34600333111")
        self.assertEqual(client.source, BusinessClient.Source.PROFESSIONAL)

    def test_professional_client_detail_is_scoped_to_business(self):
        self.client.force_login(self.professional)
        client = BusinessClient.objects.get(business=self.business, full_name="Lucia Gomez")
        other_client = BusinessClient.objects.get(business=self.other_business, full_name="Javier Martin")

        response = self.client.get(reverse("customers:professional_client_detail", args=[client.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ficha de cliente")
        self.assertContains(response, "Lucia Gomez")
        self.assertContains(response, "Próximas citas")
        self.assertContains(response, "Historial")
        self.assertContains(response, "Personas autorizadas")

        response = self.client.get(reverse("customers:professional_client_detail", args=[other_client.id]))
        self.assertEqual(response.status_code, 404)

    def test_quick_client_from_appointment_assistant_selects_new_client(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            reverse("booking:appointment_assistant"),
            {
                "action": "quick_client",
                "full_name": "Nuria Soler",
                "phone": "600333222",
                "manual_channel": "telefono",
                "target_date": "2026-07-09",
            },
        )

        client = BusinessClient.objects.get(business=self.business, full_name="Nuria Soler")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"business_client={client.id}", response["Location"])
        self.assertIn("manual_channel=telefono", response["Location"])
        self.assertIn("target_date=2026-07-09", response["Location"])

    def test_professional_edit_form_is_preloaded(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Maria Lopez",
        )

        response = self.client.get(
            reverse("customers:professional_client_edit", args=[business_client.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="Maria Lopez"')
        self.assertContains(response, 'value="600111201"')

    def test_professional_can_edit_client_and_sync_online_phone(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Maria Lopez",
        )
        appointments_before = business_client.appointments.count()

        response = self.client.post(
            reverse("customers:professional_client_edit", args=[business_client.id]),
            {
                "full_name": "Maria Lopez Romero",
                "phone": "600 333 444",
                "email": "maria@example.local",
                "internal_notes": "Prefiere las primeras horas.",
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[business_client.id]),
        )
        business_client.refresh_from_db()
        business_client.access.refresh_from_db()
        self.assertEqual(business_client.full_name, "Maria Lopez Romero")
        self.assertEqual(business_client.phone_normalized, "+34600333444")
        self.assertEqual(business_client.access.phone_normalized, "+34600333444")
        self.assertEqual(business_client.appointments.count(), appointments_before)

    def test_professional_edit_rejects_phone_used_by_other_online_account(self):
        self.client.force_login(self.professional)
        maria = BusinessClient.objects.get(business=self.business, full_name="Maria Lopez")
        lucia = BusinessClient.objects.get(business=self.business, full_name="Lucia Gomez")

        response = self.client.post(
            reverse("customers:professional_client_edit", args=[maria.id]),
            {
                "full_name": maria.full_name,
                "phone": lucia.phone,
                "email": "",
                "internal_notes": maria.internal_notes,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "otra cuenta online")
        maria.refresh_from_db()
        self.assertEqual(maria.phone_normalized, "+34600111201")

    def test_professional_can_pause_and_reactivate_client_without_losing_history(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Carmen Ruiz",
        )
        appointments_before = business_client.appointments.count()
        toggle_url = reverse(
            "customers:professional_client_toggle",
            args=[business_client.id],
        )

        response = self.client.post(toggle_url)

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[business_client.id]),
        )
        business_client.refresh_from_db()
        self.assertFalse(business_client.is_active)
        self.assertEqual(business_client.appointments.count(), appointments_before)
        inactive_list = self.client.get(
            reverse("customers:professional_client_list") + "?status=inactive"
        )
        self.assertContains(inactive_list, "Carmen Ruiz")

        self.client.post(toggle_url)
        business_client.refresh_from_db()
        self.assertTrue(business_client.is_active)

    def test_professional_cannot_pause_client_with_pending_confirmed_appointment(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Carmen Ruiz",
        )
        starts_at = timezone.now() + timedelta(days=30)
        Appointment.objects.create(
            business=self.business,
            business_client=business_client,
            work_line=self.business.work_lines.filter(is_active=True).first(),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(minutes=30),
            total_duration_minutes=30,
            status=Appointment.Status.CONFIRMED,
            manual_channel=Appointment.ManualChannel.PHONE,
            created_by=self.professional,
        )

        response = self.client.post(
            reverse("customers:professional_client_toggle", args=[business_client.id]),
            follow=True,
        )

        business_client.refresh_from_db()
        self.assertTrue(business_client.is_active)
        self.assertContains(response, "citas confirmadas pendientes")

    def test_professional_can_add_contact_and_replace_primary_contact(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucia Gomez",
        )
        previous_primary = business_client.authorized_contacts.get(is_primary_contact=True)

        create_page = self.client.get(
            reverse("customers:professional_contact_create", args=[business_client.id])
        )
        self.assertContains(create_page, "Selecciona la relación")

        response = self.client.post(
            reverse("customers:professional_contact_create", args=[business_client.id]),
            {
                "full_name": "Carlos Gomez",
                "phone": "600111255",
                "relationship_label": BusinessClientAuthorizedContact.Relationship.FATHER,
                "is_primary_contact": "on",
                "notes": "Puede confirmar cambios de horario.",
            },
        )

        self.assertRedirects(
            response,
            reverse("customers:professional_client_detail", args=[business_client.id]),
        )
        new_primary = business_client.authorized_contacts.get(full_name="Carlos Gomez")
        previous_primary.refresh_from_db()
        self.assertTrue(new_primary.is_primary_contact)
        self.assertFalse(previous_primary.is_primary_contact)

    def test_professional_can_edit_pause_and_reactivate_authorized_contact(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Lucia Gomez",
        )
        contact = business_client.authorized_contacts.get(full_name="Ana Gomez")
        edit_url = reverse(
            "customers:professional_contact_edit",
            args=[business_client.id, contact.id],
        )

        edit_page = self.client.get(edit_url)
        self.assertContains(edit_page, 'value="Ana Gomez"')
        response = self.client.post(
            edit_url,
            {
                "full_name": "Ana Gomez Ruiz",
                "phone": contact.phone,
                "relationship_label": contact.relationship_label,
                "is_primary_contact": "on",
                "notes": "Llamar si cambia la hora.",
            },
        )
        self.assertEqual(response.status_code, 302)
        contact.refresh_from_db()
        self.assertEqual(contact.full_name, "Ana Gomez Ruiz")

        toggle_url = reverse(
            "customers:professional_contact_toggle",
            args=[business_client.id, contact.id],
        )
        self.client.post(toggle_url)
        contact.refresh_from_db()
        self.assertFalse(contact.is_active)
        self.client.post(toggle_url)
        contact.refresh_from_db()
        self.assertTrue(contact.is_active)

    def test_professional_contact_routes_are_scoped_to_business(self):
        self.client.force_login(self.professional)
        other_client = BusinessClient.objects.get(
            business=self.other_business,
            full_name="Javier Martin",
        )
        other_contact = BusinessClientAuthorizedContact.objects.create(
            business=self.other_business,
            business_client=other_client,
            full_name="Marta Martin",
            phone="600999101",
        )

        response = self.client.get(
            reverse(
                "customers:professional_contact_edit",
                args=[other_client.id, other_contact.id],
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_professional_can_pause_and_reactivate_online_account(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Maria Lopez",
        )
        toggle_url = reverse(
            "customers:professional_client_access_toggle",
            args=[business_client.id],
        )

        self.assertEqual(self.client.get(toggle_url).status_code, 405)
        self.client.post(toggle_url)
        business_client.access.refresh_from_db()
        self.assertFalse(business_client.access.is_active)
        self.client.post(toggle_url)
        business_client.access.refresh_from_db()
        self.assertTrue(business_client.access.is_active)

    def test_inactive_client_is_not_available_in_appointment_assistant(self):
        self.client.force_login(self.professional)
        business_client = BusinessClient.objects.get(
            business=self.business,
            full_name="Carmen Ruiz",
        )
        business_client.is_active = False
        business_client.save(update_fields=["is_active", "updated_at"])

        response = self.client.get(reverse("booking:appointment_assistant"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, ">Carmen Ruiz</option>")

# Create your tests here.

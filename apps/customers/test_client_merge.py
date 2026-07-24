from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.booking.forms import AppointmentSearchForm
from apps.booking.models import Appointment, WorkLine
from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessGrant,
)
from apps.customers.services import (
    dismiss_client_merge_candidate,
    get_bookable_clients,
    get_client_merge_candidate,
    get_client_merge_candidates,
    merge_client_records,
)
from apps.legal.models import CustomerPrivacyEvidence, LegalAcceptance
from apps.legal.services import (
    acknowledge_customer_privacy,
    customer_privacy_status,
)
from apps.notifications.models import InternalNotification


class ClientMergeWorkflowTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Salón coincidencias",
            slug="salon-coincidencias",
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600910001",
            password="clave-segura",
            full_name="Profesional de pruebas",
            email="profesional@example.com",
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
        )
        self.professional_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Pepito Pérez",
            phone="600 910 020",
            email="pepito@example.com",
            source=BusinessClient.Source.PROFESSIONAL,
        )
        self.online_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Pepito Pérez",
            phone="+34 600 910 020",
            email="pepito@example.com",
            source=BusinessClient.Source.OTHER,
        )
        self.access = BusinessClientAccess(
            business=self.business,
            business_client=self.online_client,
            phone=self.online_client.phone,
            email=self.online_client.email,
            email_verified_at=timezone.now(),
            is_active=True,
            is_pending_public_registration=False,
        )
        self.access.set_password("clave-cliente")
        self.access.full_clean()
        self.access.save()
        BusinessClientAccessGrant.objects.create(
            business=self.business,
            access=self.access,
            business_client=self.online_client,
            relationship_label=BusinessClientAccessGrant.Relationship.SELF,
        )

    def _candidate(self):
        return get_client_merge_candidate(
            business=self.business,
            professional_client_id=self.professional_client.pk,
            online_client_id=self.online_client.pk,
        )

    def test_only_an_exact_verified_pair_becomes_a_private_candidate(self):
        candidate = self._candidate()

        self.assertIsNotNone(candidate)
        self.assertEqual(
            get_client_merge_candidates(business=self.business),
            (candidate,),
        )

        self.access.email_verified_at = None
        self.access.save(update_fields=["email_verified_at", "updated_at"])
        self.assertIsNone(self._candidate())

        self.access.email_verified_at = timezone.now()
        self.access.save(update_fields=["email_verified_at", "updated_at"])
        self.online_client.email = "otra-persona@example.com"
        self.online_client.save(update_fields=["email", "updated_at"])
        self.assertIsNone(self._candidate())

    def test_dismissal_is_persistent_until_the_exact_identity_changes(self):
        dismiss_client_merge_candidate(
            business=self.business,
            professional_client_id=self.professional_client.pk,
            online_client_id=self.online_client.pk,
            actor=self.professional,
        )

        self.assertEqual(get_client_merge_candidates(business=self.business), ())
        self.assertIsNotNone(
            get_client_merge_candidate(
                business=self.business,
                professional_client_id=self.professional_client.pk,
                online_client_id=self.online_client.pk,
                include_dismissed=True,
            )
        )

        for client in (self.professional_client, self.online_client):
            client.full_name = "Pepito Pérez García"
            client.save(update_fields=["full_name", "full_name_normalized", "updated_at"])
        self.assertIsNotNone(self._candidate())

    def test_merge_preserves_account_appointments_notifications_and_trace(self):
        work_line = WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Puesto 1",
        )
        start = timezone.now() + timedelta(days=2)
        for offset, client in enumerate(
            (self.professional_client, self.online_client)
        ):
            starts_at = start + timedelta(hours=offset)
            Appointment.objects.create(
                business=self.business,
                business_client=client,
                work_line=work_line,
                starts_at=starts_at,
                ends_at=starts_at + timedelta(minutes=30),
                total_duration_minutes=30,
            )
        notification = InternalNotification.objects.create(
            business=self.business,
            business_client=self.online_client,
            channel=InternalNotification.Channel.INTERNAL,
            event_type=InternalNotification.EventType.INTERNAL_REMINDER,
            content="Aviso de prueba",
        )

        result = merge_client_records(
            business=self.business,
            professional_client_id=self.professional_client.pk,
            online_client_id=self.online_client.pk,
            actor=self.professional,
        )

        self.assertEqual(result.appointments_moved, 1)
        self.assertEqual(result.notifications_moved, 1)
        self.professional_client.refresh_from_db()
        self.online_client.refresh_from_db()
        self.access.refresh_from_db()
        notification.refresh_from_db()
        self.assertEqual(self.professional_client.appointments.count(), 2)
        self.assertEqual(self.access.business_client, self.professional_client)
        self.assertEqual(notification.business_client, self.professional_client)
        self.assertFalse(self.online_client.is_active)
        self.assertEqual(self.online_client.merged_into, self.professional_client)
        self.assertEqual(
            list(get_bookable_clients(self.access)),
            [self.professional_client],
        )
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                event_type=BusinessActivityEvent.EventType.CLIENT_RECORDS_MERGED,
                entity_id=self.professional_client.pk,
            ).exists()
        )
        with self.assertRaises(ValidationError):
            merge_client_records(
                business=self.business,
                professional_client_id=self.professional_client.pk,
                online_client_id=self.online_client.pk,
                actor=self.professional,
            )

    def test_candidate_records_are_withheld_from_new_appointment_selection(self):
        form = AppointmentSearchForm(business=self.business)

        self.assertTrue(form.has_client_merge_candidates)
        self.assertEqual(
            list(form.fields["business_client"].queryset.values_list("pk", flat=True)),
            [],
        )

        merge_client_records(
            business=self.business,
            professional_client_id=self.professional_client.pk,
            online_client_id=self.online_client.pk,
            actor=self.professional,
        )
        form = AppointmentSearchForm(business=self.business)
        self.assertFalse(form.has_client_merge_candidates)
        self.assertEqual(
            list(form.fields["business_client"].queryset.values_list("pk", flat=True)),
            [self.professional_client.pk],
        )

    def test_online_privacy_history_remains_valid_and_visible_after_merge(self):
        acknowledge_customer_privacy(
            client_access=self.access,
            context=LegalAcceptance.Context.CLIENT_REGISTRATION,
            action_fingerprint_source="client-merge-history-test",
        )
        evidence = CustomerPrivacyEvidence.objects.get(
            business_client=self.online_client,
            client_access=self.access,
        )

        merge_client_records(
            business=self.business,
            professional_client_id=self.professional_client.pk,
            online_client_id=self.online_client.pk,
            actor=self.professional,
        )

        evidence.refresh_from_db()
        evidence.full_clean()
        status = customer_privacy_status(self.professional_client)
        self.assertTrue(
            any(
                event.business_client_id == self.online_client.pk
                and event.client_access_id == self.access.pk
                for event in status["history"]
            )
        )

    def test_professional_can_review_merge_and_old_detail_redirects(self):
        self.client.force_login(self.professional)
        list_response = self.client.get(
            reverse("customers:professional_client_list")
        )
        review_url = reverse(
            "customers:professional_client_merge_review",
            args=[self.professional_client.pk, self.online_client.pk],
        )

        self.assertContains(list_response, "Coincidencias por revisar")
        self.assertContains(list_response, "Creada en el negocio")
        self.assertContains(list_response, "Cuenta online verificada")
        self.assertContains(self.client.get(review_url), "Una sola ficha")

        confirm_response = self.client.post(
            reverse(
                "customers:professional_client_merge_confirm",
                args=[self.professional_client.pk, self.online_client.pk],
            )
        )
        self.assertRedirects(
            confirm_response,
            reverse(
                "customers:professional_client_detail",
                args=[self.professional_client.pk],
            ),
        )
        archived_response = self.client.get(
            reverse(
                "customers:professional_client_detail",
                args=[self.online_client.pk],
            )
        )
        self.assertRedirects(
            archived_response,
            reverse(
                "customers:professional_client_detail",
                args=[self.professional_client.pk],
            ),
        )

    def test_cross_tenant_actor_cannot_dismiss_or_merge(self):
        other_business = Business.objects.create(
            commercial_name="Otro salón",
            slug="otro-salon",
        )
        other_user = get_user_model().objects.create_user(
            normalized_phone="+34600910002",
            password="clave-segura",
            full_name="Otra profesional",
            email="otra@example.com",
        )
        BusinessMembership.objects.create(
            business=other_business,
            user=other_user,
        )

        with self.assertRaises(ValidationError):
            dismiss_client_merge_candidate(
                business=self.business,
                professional_client_id=self.professional_client.pk,
                online_client_id=self.online_client.pk,
                actor=other_user,
            )
        with self.assertRaises(ValidationError):
            merge_client_records(
                business=self.business,
                professional_client_id=self.professional_client.pk,
                online_client_id=self.online_client.pk,
                actor=other_user,
            )

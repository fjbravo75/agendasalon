from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from unittest.mock import patch
import uuid

from django.core.exceptions import ValidationError
from django.db import connections, transaction
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.businesses.models import Business
from apps.customers.models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessGrant,
)
from apps.customers.services import (
    purge_expired_public_registrations,
    register_client_access,
)
from apps.notifications.models import OutboundEmail
from apps.notifications.services import (
    _is_still_valid,
    client_verification_token,
    queue_client_email_verification,
    verified_client_from_token,
)


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLPendingPublicRegistrationConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Salón P2 concurrente",
            slug="salon-p2-concurrente",
            is_active=True,
            public_booking_enabled=True,
        )

    def test_two_simultaneous_registrations_leave_one_consistent_identity(self):
        barrier = Barrier(2, timeout=5)
        payloads = (
            ("Nombre A", "600882001"),
            ("Nombre B", "600882002"),
        )

        def register_in_own_connection(payload):
            connections.close_all()
            try:
                business = Business.objects.get(pk=self.business.pk)
                barrier.wait()
                try:
                    access = register_client_access(
                        business=business,
                        full_name=payload[0],
                        phone=payload[1],
                        email="doble-alta@example.test",
                    )
                except ValidationError:
                    return None
                return access.pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(register_in_own_connection, payload)
                for payload in payloads
            ]
            results = [future.result(timeout=10) for future in futures]

        self.assertEqual(sum(result is not None for result in results), 1)
        access = BusinessClientAccess.objects.get(
            business=self.business,
            email_normalized="doble-alta@example.test",
        )
        access.business_client.refresh_from_db()
        self.assertIn(
            (access.business_client.full_name, access.phone),
            payloads,
        )
        self.assertEqual(self.business.clients.count(), 1)
        self.assertEqual(
            BusinessClientAccessGrant.objects.filter(access=access).count(),
            1,
        )
        self.assertFalse(OutboundEmail.objects.exists())

    def test_purge_racing_new_registration_leaves_only_the_fresh_graph(self):
        old_access = register_client_access(
            business=self.business,
            full_name="Nombre vencido",
            phone="600882010",
            email="reutilizable@example.test",
        )
        old_client_id = old_access.business_client_id
        old_email_id = queue_client_email_verification(old_access).pk
        BusinessClientAccess.objects.filter(pk=old_access.pk).update(
            public_registration_expires_at=timezone.now() - timedelta(seconds=1)
        )
        barrier = Barrier(2, timeout=5)

        def purge_in_own_connection():
            connections.close_all()
            try:
                barrier.wait()
                result = purge_expired_public_registrations(
                    business_id=self.business.pk,
                    batch_size=10,
                )
                return result.purged
            finally:
                connections.close_all()

        def register_in_own_connection():
            connections.close_all()
            try:
                business = Business.objects.get(pk=self.business.pk)
                barrier.wait()
                access = register_client_access(
                    business=business,
                    full_name="Nombre nuevo",
                    phone="600882011",
                    email="reutilizable@example.test",
                )
                return access.pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            purge_future = executor.submit(purge_in_own_connection)
            registration_future = executor.submit(register_in_own_connection)
            purge_future.result(timeout=10)
            new_access_id = registration_future.result(timeout=10)

        access = BusinessClientAccess.objects.select_related("business_client").get(
            pk=new_access_id
        )
        self.assertEqual(access.business_client.full_name, "Nombre nuevo")
        self.assertEqual(access.phone, "600882011")
        self.assertTrue(access.is_pending_public_registration)
        self.assertGreater(access.public_registration_expires_at, timezone.now())
        self.assertFalse(BusinessClient.objects.filter(pk=old_client_id).exists())
        self.assertFalse(BusinessClientAccess.objects.filter(pk=old_access.pk).exists())
        self.assertFalse(OutboundEmail.objects.filter(pk=old_email_id).exists())
        self.assertEqual(self.business.clients.count(), 1)
        self.assertEqual(
            BusinessClientAccessGrant.objects.filter(access=access).count(),
            1,
        )

    def test_verification_racing_purge_leaves_one_coherent_final_state(self):
        access = register_client_access(
            business=self.business,
            full_name="Nombre por verificar",
            phone="600882020",
            email="verificacion-carrera@example.test",
        )
        client_id = access.business_client_id
        token = client_verification_token(access)
        email = queue_client_email_verification(access)
        expiry = timezone.now() + timedelta(seconds=30)
        BusinessClientAccess.objects.filter(pk=access.pk).update(
            public_registration_expires_at=expiry
        )
        barrier = Barrier(2, timeout=5)

        def verify_in_own_connection():
            connections.close_all()
            try:
                business = Business.objects.get(pk=self.business.pk)
                barrier.wait()
                verified = verified_client_from_token(
                    token,
                    business=business,
                    # gitleaks:allow -- Credencial ficticia limitada a esta prueba aislada.
                    password="Clave-segura-2026",  # gitleaks:allow
                )
                return verified.pk if verified is not None else None
            finally:
                connections.close_all()

        def purge_in_own_connection():
            connections.close_all()
            try:
                barrier.wait()
                return purge_expired_public_registrations(
                    business_id=self.business.pk,
                    now=expiry + timedelta(seconds=1),
                    batch_size=10,
                ).purged
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            verification_future = executor.submit(verify_in_own_connection)
            purge_future = executor.submit(purge_in_own_connection)
            verified_id = verification_future.result(timeout=10)
            purged = purge_future.result(timeout=10)

        final_access = BusinessClientAccess.objects.select_related(
            "business_client"
        ).filter(pk=access.pk).first()
        if final_access is None:
            self.assertIsNone(verified_id)
            self.assertEqual(purged, 1)
            self.assertFalse(BusinessClient.objects.filter(pk=client_id).exists())
            self.assertFalse(OutboundEmail.objects.filter(pk=email.pk).exists())
            return

        self.assertEqual(verified_id, final_access.pk)
        self.assertEqual(purged, 0)
        self.assertTrue(final_access.business_client.is_active)
        self.assertIsNotNone(final_access.email_verified_at)
        self.assertFalse(final_access.is_pending_public_registration)
        self.assertIsNone(final_access.public_registration_expires_at)
        queued_email = OutboundEmail.objects.get(pk=email.pk)
        self.assertFalse(_is_still_valid(queued_email))
        self.assertEqual(
            BusinessClientAccessGrant.objects.filter(access=final_access).count(),
            1,
        )

    def test_token_profile_correction_racing_purge_is_atomic(self):
        access = register_client_access(
            business=self.business,
            full_name="Nombre antes de carrera",
            phone="600882030",
            email="correccion-carrera@example.test",
        )
        client_id = access.business_client_id
        token = client_verification_token(access)
        email = queue_client_email_verification(access)
        expiry = timezone.now() + timedelta(seconds=30)
        BusinessClientAccess.objects.filter(pk=access.pk).update(
            public_registration_expires_at=expiry
        )
        barrier = Barrier(2, timeout=5)

        def correct_in_own_connection():
            connections.close_all()
            try:
                business = Business.objects.get(pk=self.business.pk)
                barrier.wait()
                corrected = verified_client_from_token(
                    token,
                    business=business,
                    # gitleaks:allow -- Credencial ficticia limitada a esta prueba aislada.
                    password="Clave-correccion-carrera-2026",  # gitleaks:allow
                    full_name="Nombre corregido en carrera",
                    phone="600882031",
                )
                return corrected.pk if corrected is not None else None
            finally:
                connections.close_all()

        def purge_in_own_connection():
            connections.close_all()
            try:
                barrier.wait()
                return purge_expired_public_registrations(
                    business_id=self.business.pk,
                    now=expiry + timedelta(seconds=1),
                    batch_size=10,
                ).purged
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            correction_future = executor.submit(correct_in_own_connection)
            purge_future = executor.submit(purge_in_own_connection)
            corrected_id = correction_future.result(timeout=10)
            purged = purge_future.result(timeout=10)

        final_access = BusinessClientAccess.objects.select_related(
            "business_client"
        ).filter(pk=access.pk).first()
        if final_access is None:
            self.assertIsNone(corrected_id)
            self.assertEqual(purged, 1)
            self.assertFalse(BusinessClient.objects.filter(pk=client_id).exists())
            self.assertFalse(OutboundEmail.objects.filter(pk=email.pk).exists())
            return

        self.assertEqual(corrected_id, final_access.pk)
        self.assertEqual(purged, 0)
        self.assertEqual(
            final_access.business_client.full_name,
            "Nombre corregido en carrera",
        )
        self.assertEqual(final_access.business_client.phone, "600882031")
        self.assertEqual(final_access.phone, "600882031")
        self.assertTrue(final_access.business_client.is_active)
        self.assertIsNotNone(final_access.email_verified_at)
        self.assertFalse(final_access.is_pending_public_registration)
        self.assertIsNone(final_access.public_registration_expires_at)
        self.assertEqual(
            BusinessClientAccessGrant.objects.filter(access=final_access).count(),
            1,
        )

    def test_processing_claim_racing_purge_is_retained_without_deadlock(self):
        access = register_client_access(
            business=self.business,
            full_name="Nombre con correo en curso",
            phone="600882040",
            email="processing-carrera@example.test",
        )
        client_id = access.business_client_id
        email = queue_client_email_verification(access)
        BusinessClientAccess.objects.filter(pk=access.pk).update(
            public_registration_expires_at=timezone.now() - timedelta(seconds=1)
        )
        processing_locked = Event()
        outbox_check_started = Event()

        def mark_processing_in_own_connection():
            connections.close_all()
            try:
                with transaction.atomic():
                    locked_email = OutboundEmail.objects.select_for_update().get(
                        pk=email.pk
                    )
                    locked_email.status = OutboundEmail.Status.PROCESSING
                    locked_email.lease_token = uuid.uuid4()
                    locked_email.lease_expires_at = timezone.now() + timedelta(minutes=5)
                    locked_email.save(
                        update_fields=[
                            "status",
                            "lease_token",
                            "lease_expires_at",
                            "updated_at",
                        ]
                    )
                    processing_locked.set()
                    if not outbox_check_started.wait(timeout=5):
                        raise TimeoutError("La purga no alcanzó el bloqueo del outbox.")
                return locked_email.pk
            finally:
                connections.close_all()

        def purge_in_own_connection():
            connections.close_all()
            try:
                if not processing_locked.wait(timeout=5):
                    raise TimeoutError("El correo no entró en PROCESSING.")
                from apps.customers import services as customer_services

                original_check = (
                    customer_services._pending_registration_outbox_is_safe_to_purge
                )

                def observed_check(locked_access, **kwargs):
                    outbox_check_started.set()
                    return original_check(locked_access, **kwargs)

                with patch(
                    "apps.customers.services._pending_registration_outbox_is_safe_to_purge",
                    side_effect=observed_check,
                ):
                    return purge_expired_public_registrations(
                        business_id=self.business.pk,
                        batch_size=10,
                    )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            processing_future = executor.submit(mark_processing_in_own_connection)
            purge_future = executor.submit(purge_in_own_connection)
            processing_future.result(timeout=10)
            result = purge_future.result(timeout=10)

        self.assertEqual(result.purged, 0)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(BusinessClient.objects.filter(pk=client_id).exists())
        self.assertTrue(BusinessClientAccess.objects.filter(pk=access.pk).exists())
        email.refresh_from_db()
        self.assertEqual(email.status, OutboundEmail.Status.PROCESSING)
        self.assertIsNotNone(email.lease_token)
        self.assertGreater(email.lease_expires_at, timezone.now())

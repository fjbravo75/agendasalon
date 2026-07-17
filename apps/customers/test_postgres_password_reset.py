from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.businesses.models import Business
from apps.customers.models import BusinessClient, BusinessClientAccess
from apps.notifications.services import (
    client_password_reset_token,
    reset_client_password_from_token,
)


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLClientPasswordResetTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Salón con reset concurrente",
            slug="salon-reset-concurrente",
            is_active=True,
        )
        self.business_client = BusinessClient.objects.create(
            business=self.business,
            full_name="Cliente con reset concurrente",
            phone="600111801",
        )
        self.access = BusinessClientAccess(
            business=self.business,
            business_client=self.business_client,
            phone="600111801",
            email="reset.concurrente@example.test",
            email_verified_at=timezone.now(),
        )
        self.access.set_password("ClienteAnterior2026!")
        self.access.full_clean()
        self.access.save()

    def test_same_reset_token_changes_the_password_only_once(self):
        token = client_password_reset_token(self.access)
        passwords = ("ClienteNuevaA2026!", "ClienteNuevaB2026!")
        start_barrier = Barrier(2, timeout=5)

        def reset_in_own_connection(password):
            connections.close_all()
            try:
                business = Business.objects.get(pk=self.business.pk)
                start_barrier.wait()
                access = reset_client_password_from_token(
                    token,
                    business=business,
                    password=password,
                )
                return access is not None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reset_in_own_connection, passwords))

        self.assertCountEqual(results, [True, False])
        self.access.refresh_from_db()
        self.assertTrue(any(self.access.check_password(password) for password in passwords))

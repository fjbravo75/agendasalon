from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError, Lock
from unittest.mock import patch

from django.db import connections
from django.test import Client, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from apps.businesses.models import Business


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLLoginThrottleConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_concurrent_burst_never_reaches_authentication_above_subject_limit(self):
        worker_count = 12
        authentication_barrier = Barrier(worker_count, timeout=1)
        authentication_calls = 0
        calls_lock = Lock()

        def fake_authenticate(*args, **kwargs):
            nonlocal authentication_calls
            with calls_lock:
                authentication_calls += 1
            try:
                authentication_barrier.wait()
            except BrokenBarrierError:
                pass
            return None

        def submit_invalid_login(_worker):
            connections.close_all()
            client = Client(REMOTE_ADDR="203.0.113.40")
            try:
                return client.post(
                    reverse("accounts:login"),
                    {
                        "username": "600 000 000",
                        "password": "clave-no-valida",
                    },
                ).status_code
            finally:
                connections.close_all()

        with patch("apps.accounts.forms.authenticate", side_effect=fake_authenticate):
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                statuses = list(executor.map(submit_invalid_login, range(worker_count)))

        self.assertEqual(authentication_calls, 5)
        self.assertEqual(len(statuses), worker_count)
        self.assertTrue(all(status in {200, 429} for status in statuses))
        self.assertGreaterEqual(statuses.count(429), worker_count - 4)

    def test_concurrent_client_burst_uses_the_same_atomic_reservation(self):
        business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )
        worker_count = 12
        authentication_barrier = Barrier(worker_count, timeout=1)
        authentication_calls = 0
        calls_lock = Lock()

        def fake_authenticate(*args, **kwargs):
            nonlocal authentication_calls
            with calls_lock:
                authentication_calls += 1
            try:
                authentication_barrier.wait()
            except BrokenBarrierError:
                pass
            return None

        def submit_invalid_login(_worker):
            connections.close_all()
            client = Client(REMOTE_ADDR="203.0.113.41")
            try:
                return client.post(
                    reverse("customers:client_access", args=[business.slug]),
                    {
                        "phone": "600 000 001",
                        "password": "clave-no-valida",
                    },
                ).status_code
            finally:
                connections.close_all()

        with patch("apps.customers.forms.authenticate_client_access", side_effect=fake_authenticate):
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                statuses = list(executor.map(submit_invalid_login, range(worker_count)))

        self.assertEqual(authentication_calls, 5)
        self.assertEqual(len(statuses), worker_count)
        self.assertTrue(all(status in {200, 429} for status in statuses))
        self.assertGreaterEqual(statuses.count(429), worker_count - 4)

    def test_concurrent_private_burst_respects_ip_limit_across_subjects(self):
        worker_count = 36
        authentication_barrier = Barrier(worker_count, timeout=1)
        authentication_calls = 0
        calls_lock = Lock()

        def fake_authenticate(*args, **kwargs):
            nonlocal authentication_calls
            with calls_lock:
                authentication_calls += 1
            try:
                authentication_barrier.wait()
            except BrokenBarrierError:
                pass
            return None

        def submit_invalid_login(worker):
            connections.close_all()
            client = Client(REMOTE_ADDR="203.0.113.42")
            try:
                return client.post(
                    reverse("accounts:login"),
                    {
                        "username": f"60000{worker:04d}",
                        "password": "clave-no-valida",
                    },
                ).status_code
            finally:
                connections.close_all()

        with patch("apps.accounts.forms.authenticate", side_effect=fake_authenticate):
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                statuses = list(executor.map(submit_invalid_login, range(worker_count)))

        self.assertEqual(authentication_calls, 30)
        self.assertEqual(len(statuses), worker_count)
        self.assertTrue(all(status in {200, 429} for status in statuses))
        self.assertGreaterEqual(statuses.count(429), worker_count - 29)

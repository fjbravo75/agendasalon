from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from apps.accounts.views import _verify_professional_email_from_token
from apps.accounts.tokens import professional_email_verification_token_generator


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLProfessionalEmailVerificationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            normalized_phone="+34600111801",
            full_name="Profesional con verificación concurrente",
            email="profesional.concurrente@example.test",
            password="CuentaPersonal2026!Segura",
            is_active=True,
            email_verification_required=True,
        )

    def test_same_token_can_verify_the_email_only_once_across_transactions(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = professional_email_verification_token_generator.make_token(self.user)
        start_barrier = Barrier(2, timeout=5)

        def verify_in_own_connection(_worker):
            connections.close_all()
            try:
                start_barrier.wait()
                user = _verify_professional_email_from_token(uid, token)
                return user.pk if user is not None else None
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(verify_in_own_connection, range(2)))

        self.assertCountEqual(results, [self.user.pk, None])
        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.email_verified_at)
        self.assertFalse(self.user.email_verification_required)

from datetime import time

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import User
from apps.booking.models import AvailabilityRule, Service, WorkLine
from apps.businesses.models import Business, BusinessMembership
from apps.businesses.services import get_primary_business_for_user


class BusinessModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            normalized_phone="+34600111001",
            password="test-pass",
            full_name="Mari Profesional",
        )
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )

    def test_business_operational_requires_minimum_setup(self):
        self.assertFalse(self.business.is_operational_for_agenda())

        Service.objects.create(
            business=self.business,
            name="Corte",
            duration_minutes=30,
        )
        AvailabilityRule.objects.create(
            business=self.business,
            weekday=0,
            start_time=time(9, 0),
            end_time=time(14, 0),
        )
        WorkLine.objects.create(
            business=self.business,
            line_number=1,
            name="Linea 1",
        )

        self.assertTrue(self.business.is_operational_for_agenda())

    def test_business_membership_is_unique_per_user_and_business(self):
        BusinessMembership.objects.create(business=self.business, user=self.user)

        with self.assertRaises(IntegrityError), transaction.atomic():
            BusinessMembership.objects.create(business=self.business, user=self.user)

    def test_professional_account_cannot_belong_to_a_second_business(self):
        BusinessMembership.objects.create(business=self.business, user=self.user)
        second_business = Business.objects.create(
            commercial_name="Peluquería Angustias",
            slug="peluqueria-angustias",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            BusinessMembership.objects.create(
                business=second_business,
                user=self.user,
            )

    def test_one_business_can_have_multiple_distinct_professional_accounts(self):
        BusinessMembership.objects.create(business=self.business, user=self.user)
        second_user = User.objects.create_user(
            normalized_phone="+34600111009",
            password="test-pass",
            full_name="Segunda Profesional",
            email="segunda-profesional@example.com",
        )

        second_membership = BusinessMembership.objects.create(
            business=self.business,
            user=second_user,
        )

        self.assertEqual(second_membership.business, self.business)
        self.assertEqual(self.business.memberships.filter(is_active=True).count(), 2)


class BusinessAccessServiceTests(TestCase):
    def test_primary_business_ignores_inactive_memberships(self):
        user = User.objects.create_user(
            normalized_phone="+34600111002",
            password="test-pass",
            full_name="Mari Profesional",
        )
        business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )
        BusinessMembership.objects.create(
            business=business,
            user=user,
            is_active=False,
        )

        self.assertIsNone(get_primary_business_for_user(user))

    def test_primary_business_returns_active_business_for_professional(self):
        user = User.objects.create_user(
            normalized_phone="+34600111003",
            password="test-pass",
            full_name="Mari Profesional",
        )
        business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )
        BusinessMembership.objects.create(business=business, user=user)

        self.assertEqual(get_primary_business_for_user(user), business)

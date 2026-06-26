from datetime import time

from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import User
from apps.booking.models import AvailabilityRule, Service, WorkLine
from apps.businesses.models import Business, BusinessMembership


class BusinessModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            normalized_phone="+34600111001",
            password="test-pass",
            full_name="Mari Profesional",
        )
        self.business = Business.objects.create(
            commercial_name="Peluqueria Mari",
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

# Create your tests here.

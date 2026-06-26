from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.businesses.models import Business
from apps.customers.models import BusinessClient, BusinessClientAuthorizedContact


class CustomerModelTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluqueria Mari",
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

# Create your tests here.

from django.test import TestCase
from django.urls import reverse

from apps.businesses.models import Business


class PublicHomeTests(TestCase):
    def test_home_lists_each_active_business_instead_of_choosing_one(self):
        mari = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )
        barberia = Business.objects.create(
            commercial_name="Barbería Norte",
            slug="barberia-norte",
            is_active=True,
        )
        Business.objects.create(
            commercial_name="Salón inactivo",
            slug="salon-inactivo",
            is_active=False,
        )

        response = self.client.get(reverse("home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "¿A dónde quieres entrar?")
        self.assertContains(response, mari.commercial_name)
        self.assertContains(response, barberia.commercial_name)
        self.assertContains(response, reverse("customers:client_access", args=[mari.slug]))
        self.assertContains(response, reverse("customers:client_access", args=[barberia.slug]))
        self.assertNotContains(response, "Salón inactivo")

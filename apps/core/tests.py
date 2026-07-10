from django.test import TestCase
from django.urls import reverse


class RootRoutingTests(TestCase):
    def test_anonymous_root_redirects_to_internal_login(self):
        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("accounts:login"))

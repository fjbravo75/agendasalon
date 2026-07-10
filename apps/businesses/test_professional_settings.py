from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.businesses.models import Business, BusinessActivityEvent, BusinessMembership


class ProfessionalSettingsTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_directory.cleanup)

        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600111001",
            phone="+34600111001",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        self.other_professional = get_user_model().objects.create_user(
            normalized_phone="+34600222001",
            phone="+34600222001",
            password="test-pass-123",
            full_name="Norte Profesional",
        )
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000001",
            phone="+34910000001",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
            public_booking_enabled=True,
        )
        self.other_business = Business.objects.create(
            commercial_name="Barbería Norte",
            slug="barberia-norte",
            is_active=True,
            public_booking_enabled=True,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
        )
        BusinessMembership.objects.create(
            business=self.other_business,
            user=self.other_professional,
        )
        self.url = reverse("business_settings:professional_settings")

    def test_settings_require_a_professional_business(self):
        self.assertRedirects(
            self.client.get(self.url),
            f"{reverse('accounts:login')}?next={self.url}",
        )

        self.client.force_login(self.superadmin)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_settings_are_available_from_the_professional_navigation(self):
        self.client.force_login(self.professional)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ajustes de Peluquería Mari")
        self.assertContains(response, "Modo de visualización")
        self.assertContains(response, "Imagen pública del negocio")
        self.assertContains(response, 'aria-current="page"')
        self.assertContains(response, "Predeterminada")

    def test_professional_can_enable_dark_mode_only_for_their_business(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            self.url,
            {"professional_theme": Business.ProfessionalTheme.DARK},
            follow=True,
        )

        self.business.refresh_from_db()
        self.other_business.refresh_from_db()
        self.assertEqual(self.business.professional_theme, Business.ProfessionalTheme.DARK)
        self.assertEqual(self.other_business.professional_theme, Business.ProfessionalTheme.LIGHT)
        self.assertContains(response, "theme-dark")
        self.assertContains(response, "Los ajustes visuales del negocio quedan guardados")
        public_response = self.client.get(reverse("public_booking", args=[self.business.slug]))
        self.assertNotContains(public_response, "theme-dark")
        self.assertTrue(
            BusinessActivityEvent.objects.filter(
                business=self.business,
                actor_user=self.professional,
                event_type=BusinessActivityEvent.EventType.VISUAL_SETTINGS_UPDATED,
            ).exists()
        )

    def test_custom_image_is_used_by_all_public_customer_surfaces(self):
        self.client.force_login(self.professional)
        image = self._image_file("fachada-mari.jpg")

        response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "public_image": image,
            },
        )

        self.assertRedirects(response, self.url)
        self.business.refresh_from_db()
        self.assertTrue(self.business.public_image.name.endswith(".jpg"))
        image_url = self.business.public_image.url
        for url in (
            reverse("public_booking", args=[self.business.slug]),
            reverse("customers:client_access", args=[self.business.slug]),
            reverse("customers:client_register", args=[self.business.slug]),
        ):
            public_response = self.client.get(url)
            self.assertEqual(public_response.status_code, 200)
            self.assertContains(public_response, image_url)

    def test_invalid_or_too_small_images_are_rejected(self):
        self.client.force_login(self.professional)

        invalid_response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "public_image": SimpleUploadedFile(
                    "falsa.jpg",
                    b"no es una imagen",
                    content_type="image/jpeg",
                ),
            },
        )
        self.assertContains(invalid_response, "Selecciona una imagen JPG, PNG o WebP válida")

        small_response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "public_image": self._image_file("pequena.png", size=(500, 300)),
            },
        )
        self.assertContains(small_response, "La imagen debe medir al menos 800 × 500 píxeles")
        self.business.refresh_from_db()
        self.assertFalse(self.business.public_image)

    def test_professional_can_restore_the_default_public_image(self):
        self.client.force_login(self.professional)
        self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "public_image": self._image_file("personalizada.webp", image_format="WEBP"),
            },
        )
        self.business.refresh_from_db()
        self.assertTrue(self.business.public_image)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.url,
                {
                    "professional_theme": Business.ProfessionalTheme.LIGHT,
                    "remove_public_image": "on",
                },
            )

        self.assertRedirects(response, self.url)
        self.business.refresh_from_db()
        self.assertFalse(self.business.public_image)
        public_response = self.client.get(reverse("public_booking", args=[self.business.slug]))
        self.assertNotContains(public_response, "/media/businesses/")

    @staticmethod
    def _image_file(name, *, size=(1200, 800), image_format="JPEG"):
        output = BytesIO()
        Image.new("RGB", size, color=(93, 67, 54)).save(output, format=image_format)
        output.seek(0)
        content_type = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }[image_format]
        return SimpleUploadedFile(name, output.read(), content_type=content_type)

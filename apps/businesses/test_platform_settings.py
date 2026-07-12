from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.businesses.models import (
    Business,
    BusinessMembership,
    PlatformLoginImage,
    PlatformSettings,
)


class PlatformSettingsTests(TestCase):
    def setUp(self):
        self.media_directory = TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_directory.cleanup)

        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000001",
            phone="+34910000001",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )
        self.professional = get_user_model().objects.create_user(
            normalized_phone="+34600111001",
            phone="+34600111001",
            password="test-pass-123",
            full_name="Mari Profesional",
        )
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            professional_theme=Business.ProfessionalTheme.DARK,
        )
        BusinessMembership.objects.create(
            business=self.business,
            user=self.professional,
        )
        self.url = reverse("platform_settings:superadmin_platform_settings")

    def _image_file(self, filename="acceso.png", size=(1200, 800)):
        image = Image.new("RGB", size, color=(34, 74, 66))
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return SimpleUploadedFile(filename, buffer.getvalue(), content_type="image/png")

    def test_platform_settings_are_restricted_to_superadmin(self):
        self.assertRedirects(
            self.client.get(self.url),
            f"{reverse('accounts:login')}?next={self.url}",
        )

        self.client.force_login(self.professional)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_settings_show_theme_and_all_standard_login_images(self):
        self.client.force_login(self.superadmin)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ajustes de AgendaSalon")
        self.assertContains(response, "Modo de visualización")
        self.assertContains(response, "Imagen de entrada a AgendaSalon")
        self.assertContains(response, "AgendaSalon")
        self.assertContains(response, "Salón luminoso")
        self.assertContains(response, "Barbería contemporánea")
        self.assertContains(response, "data-public-image-choice", count=3)
        self.assertContains(response, "Seleccionar imagen")
        self.assertContains(response, "Ningún archivo seleccionado")
        self.assertContains(response, 'aria-current="page"')

    def test_superadmin_can_change_theme_and_standard_login_image(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(
            self.url,
            {
                "admin_theme": PlatformSettings.AdminTheme.DARK,
                "login_image_choice": "preset:barberia",
            },
            follow=True,
        )

        self.assertContains(response, "Los ajustes de AgendaSalon quedan guardados.")
        self.assertContains(response, "theme-dark")
        settings = PlatformSettings.objects.get(pk=PlatformSettings.SINGLETON_PK)
        self.assertEqual(settings.admin_theme, PlatformSettings.AdminTheme.DARK)
        self.assertEqual(
            settings.login_image_preset,
            PlatformSettings.LoginImagePreset.BARBERSHOP,
        )
        self.assertEqual(settings.updated_by, self.superadmin)
        self.business.refresh_from_db()
        self.assertEqual(self.business.professional_theme, Business.ProfessionalTheme.DARK)

        self.client.logout()
        login_response = self.client.get(reverse("accounts:login"))
        self.assertContains(login_response, "customer-login-barberia-norte-bg-v2.png")

    def test_saving_the_same_platform_appearance_reports_no_pending_changes(self):
        PlatformSettings.objects.create(updated_by=self.superadmin)
        self.client.force_login(self.superadmin)

        response = self.client.post(
            self.url,
            {
                "admin_theme": PlatformSettings.AdminTheme.LIGHT,
                "login_image_choice": "preset:agendasalon",
            },
            follow=True,
        )

        self.assertContains(response, "No había cambios pendientes en la apariencia.")

    def test_superadmin_can_upload_and_reuse_a_custom_login_image(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(
            self.url,
            {
                "admin_theme": PlatformSettings.AdminTheme.LIGHT,
                "login_image_choice": "preset:agendasalon",
                "new_login_image": self._image_file(),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        image = PlatformLoginImage.objects.get()
        self.assertTrue(image.is_selected)
        self.assertEqual(image.uploaded_by, self.superadmin)
        self.assertContains(response, image.image.url)
        self.assertContains(response, "Personalizada")

        response = self.client.post(
            self.url,
            {
                "admin_theme": PlatformSettings.AdminTheme.LIGHT,
                "login_image_choice": "preset:salon",
            },
            follow=True,
        )
        image.refresh_from_db()
        self.assertFalse(image.is_selected)
        self.assertContains(response, image.image.url)
        self.assertContains(response, "Predeterminada")

        response = self.client.post(
            self.url,
            {
                "admin_theme": PlatformSettings.AdminTheme.LIGHT,
                "login_image_choice": f"custom:{image.pk}",
            },
            follow=True,
        )
        image.refresh_from_db()
        self.assertTrue(image.is_selected)
        self.assertContains(response, "Personalizada")

    def test_small_platform_image_is_rejected_without_saving_it(self):
        self.client.force_login(self.superadmin)

        response = self.client.post(
            self.url,
            {
                "admin_theme": PlatformSettings.AdminTheme.LIGHT,
                "login_image_choice": "preset:agendasalon",
                "new_login_image": self._image_file(size=(500, 300)),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "La imagen debe medir al menos 800 × 500 píxeles.")
        self.assertFalse(PlatformLoginImage.objects.exists())

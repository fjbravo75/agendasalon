from io import BytesIO
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.businesses.models import (
    Business,
    BusinessMembership,
    PlatformLoginImage,
    PlatformSettings,
)
from apps.holidays.models import HolidaySyncRun, OfficialHoliday


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
        self.assertContains(response, "Festivos nacionales")
        self.assertContains(response, "Sincronizar con BOE")

    def test_holiday_panel_lists_catalog_and_last_run(self):
        OfficialHoliday.objects.create(
            date="2026-01-01",
            name="Año Nuevo",
            scope=OfficialHoliday.Scope.NATIONAL,
            year=2026,
            source_name="BOE - calendario laboral nacional",
        )
        HolidaySyncRun.objects.create(
            year=2026,
            source_name="BOE - calendario laboral nacional",
            source_url="https://www.boe.es/diario_boe/txt.php?id=BOE-A-2025-21667",
            official_reference="BOE-A-2025-21667",
            status=HolidaySyncRun.Status.SUCCESS,
            started_at=timezone.now(),
            finished_at=timezone.now(),
            items_loaded=1,
            items_created=1,
        )
        self.client.force_login(self.superadmin)

        response = self.client.get(f"{self.url}?holiday_year=2026")

        self.assertContains(response, "Año Nuevo")
        self.assertContains(response, "BOE-A-2025-21667")
        self.assertContains(response, "Cargados")

    @patch("apps.businesses.views.sync_boe_national_holidays")
    def test_only_superadmin_can_trigger_holiday_sync(self, mocked_sync):
        run = SimpleNamespace(
            items_created=8,
            items_updated=0,
            items_removed=0,
            affected_appointments=0,
            affected_businesses=0,
        )
        mocked_sync.return_value = SimpleNamespace(run=run)
        sync_url = reverse("platform_settings:superadmin_holiday_sync")

        self.client.force_login(self.professional)
        self.assertEqual(self.client.post(sync_url, {"year": 2026}).status_code, 403)

        self.client.force_login(self.superadmin)
        response = self.client.post(sync_url, {"year": 2026})

        self.assertEqual(response.status_code, 302)
        self.assertIn("holiday_year=2026", response["Location"])
        mocked_sync.assert_called_once_with(2026, created_by=self.superadmin)

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
        self.assertContains(response, "superadmin-shell")
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
        self.assertContains(login_response, "customer-login-barberia-norte-bg-v2.webp")

    def test_dark_theme_reaches_the_whole_superadmin_shell(self):
        PlatformSettings.objects.create(
            admin_theme=PlatformSettings.AdminTheme.DARK,
            updated_by=self.superadmin,
        )
        self.client.force_login(self.superadmin)

        for url in (
            reverse("businesses:superadmin_business_list"),
            reverse("businesses:superadmin_business_detail", args=[self.business.pk]),
            reverse("businesses:superadmin_business_edit", args=[self.business.pk]),
            self.url,
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "superadmin-shell")
                self.assertContains(response, "theme-dark")

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

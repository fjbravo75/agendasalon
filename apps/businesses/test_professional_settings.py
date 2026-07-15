from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from apps.businesses.models import (
    Business,
    BusinessActivityEvent,
    BusinessMembership,
    BusinessPublicImage,
)


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
            public_image_preset=Business.PublicImagePreset.BARBERSHOP,
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
        self.assertContains(response, "Salón luminoso")
        self.assertContains(response, "Barbería contemporánea")
        self.assertContains(response, "data-public-image-choice", count=2)
        self.assertContains(response, "Seleccionar imagen")
        self.assertContains(response, "Ningún archivo seleccionado")

    def test_saving_the_same_business_appearance_reports_no_pending_changes(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "public_image_choice": "preset:salon",
            },
            follow=True,
        )

        self.assertContains(response, "No había cambios pendientes en la apariencia.")

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
        self.assertContains(public_response, "theme-auto")
        self.assertContains(public_response, "/static/js/public_booking.js")
        other_public_response = self.client.get(
            reverse("public_booking", args=[self.other_business.slug])
        )
        self.assertContains(other_public_response, "theme-auto")
        self.assertContains(other_public_response, "public-booking-body--barberia")
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
                "new_public_image": image,
            },
        )

        self.assertRedirects(response, self.url)
        self.business.refresh_from_db()
        selected_image = self.business.public_images.get(is_selected=True)
        self.assertTrue(selected_image.image.name.endswith(".webp"))
        image_url = selected_image.image.url
        for url in (
            reverse("public_booking", args=[self.business.slug]),
            reverse("customers:client_access", args=[self.business.slug]),
            reverse("customers:client_register", args=[self.business.slug]),
        ):
            public_response = self.client.get(url)
            self.assertEqual(public_response.status_code, 200)
            self.assertContains(public_response, image_url)

    def test_uploaded_image_is_reencoded_without_exif_metadata(self):
        self.client.force_login(self.professional)
        output = BytesIO()
        source = Image.new("RGB", (800, 1200), color=(93, 67, 54))
        exif = Image.Exif()
        exif[274] = 6
        exif[315] = "Dato interno que no debe publicarse"
        source.save(output, format="JPEG", exif=exif)
        upload = SimpleUploadedFile(
            "foto-con-metadatos.jpg",
            output.getvalue(),
            content_type="image/jpeg",
        )

        response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "new_public_image": upload,
            },
        )

        self.assertRedirects(response, self.url)
        self.business.refresh_from_db()
        selected_image = self.business.public_images.get(is_selected=True)
        self.assertTrue(selected_image.image.name.endswith(".webp"))
        with Image.open(selected_image.image.path) as stored:
            self.assertEqual(stored.format, "WEBP")
            self.assertEqual(stored.size, (1200, 800))
            self.assertFalse(stored.getexif())
            self.assertNotIn("exif", stored.info)

    def test_large_image_is_downscaled_without_changing_its_aspect_ratio(self):
        self.client.force_login(self.professional)

        response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "new_public_image": self._image_file(
                    "fachada-grande.png",
                    size=(4000, 2500),
                    image_format="PNG",
                ),
            },
        )

        self.assertRedirects(response, self.url)
        self.business.refresh_from_db()
        selected_image = self.business.public_images.get(is_selected=True)
        with Image.open(selected_image.image.path) as stored:
            self.assertEqual(stored.size, (2400, 1500))

    def test_invalid_or_too_small_images_are_rejected(self):
        self.client.force_login(self.professional)

        invalid_response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "new_public_image": SimpleUploadedFile(
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
                "new_public_image": self._image_file("pequena.png", size=(500, 300)),
            },
        )
        self.assertContains(small_response, "La imagen debe medir al menos 800 × 500 píxeles")
        self.business.refresh_from_db()
        self.assertFalse(self.business.public_images.exists())

    def test_image_over_resource_budget_is_rejected_before_processing(self):
        self.client.force_login(self.professional)

        with patch("apps.businesses.forms.sanitize_public_image") as sanitizer:
            response = self.client.post(
                self.url,
                {
                    "professional_theme": Business.ProfessionalTheme.LIGHT,
                    "new_public_image": self._image_file(
                        "demasiado-grande.png",
                        size=(4001, 4000),
                        image_format="PNG",
                    ),
                },
            )

        self.assertContains(response, "La imagen tiene demasiados píxeles para un uso seguro")
        sanitizer.assert_not_called()

    def test_professional_can_choose_between_both_default_public_images(self):
        self.client.force_login(self.professional)
        self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "new_public_image": self._image_file("personalizada.webp", image_format="WEBP"),
            },
        )
        self.business.refresh_from_db()
        self.assertTrue(self.business.public_images.filter(is_selected=True).exists())
        self.business.public_image.name = "businesses/legacy/publica.webp"
        self.business.save(update_fields=["public_image", "updated_at"])

        response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "public_image_choice": "preset:barberia",
            },
        )

        self.assertRedirects(response, self.url)
        self.business.refresh_from_db()
        self.assertEqual(
            self.business.public_image_preset,
            Business.PublicImagePreset.BARBERSHOP,
        )
        self.assertFalse(self.business.public_images.filter(is_selected=True).exists())
        public_response = self.client.get(reverse("public_booking", args=[self.business.slug]))
        self.assertNotContains(public_response, "/media/businesses/")
        self.assertContains(public_response, "customer-login-barberia-norte-bg-v2.webp")

    def test_uploaded_images_remain_available_in_the_business_gallery(self):
        self.client.force_login(self.professional)

        for filename in ("fachada-principal.jpg", "zona-lavacabezas.jpg"):
            response = self.client.post(
                self.url,
                {
                    "professional_theme": Business.ProfessionalTheme.LIGHT,
                    "new_public_image": self._image_file(filename),
                },
            )
            self.assertRedirects(response, self.url)

        images = self.business.public_images.order_by("created_at")
        self.assertEqual(images.count(), 2)
        self.assertEqual(images.filter(is_selected=True).count(), 1)

        response = self.client.get(self.url)
        self.assertContains(response, "fachada-principal")
        self.assertContains(response, "zona-lavacabezas")
        self.assertContains(response, "data-public-image-choice", count=4)

    def test_thirteenth_business_image_is_rejected_without_persisting_a_file(self):
        for index in range(12):
            BusinessPublicImage.objects.create(
                business=self.business,
                image=self._image_file(f"galeria-{index}.jpg"),
                label=f"Galería {index}",
                is_selected=index == 11,
                uploaded_by=self.professional,
            )
        stored_names = set(
            self.business.public_images.values_list("image", flat=True)
        )
        self.client.force_login(self.professional)

        response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "new_public_image": self._image_file("imagen-decimotercera.jpg"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "Este negocio ya tiene 12 imágenes guardadas. Elige una de ellas para continuar.",
        )
        self.assertEqual(self.business.public_images.count(), 12)
        self.assertSetEqual(
            set(self.business.public_images.values_list("image", flat=True)),
            stored_names,
        )

        page = self.client.get(self.url)
        self.assertContains(page, "Galería completa")
        self.assertContains(page, "12 de 12 imágenes guardadas")
        self.assertContains(page, "Has alcanzado el límite de 12 imágenes")
        self.assertContains(page, 'data-public-image-upload="" disabled')

    def test_uploaded_file_is_removed_if_the_database_transaction_rolls_back(self):
        self.client.force_login(self.professional)

        with (
            patch(
                "apps.businesses.views.record_business_activity",
                side_effect=RuntimeError("fallo posterior a la escritura"),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.client.post(
                self.url,
                {
                    "professional_theme": Business.ProfessionalTheme.LIGHT,
                    "new_public_image": self._image_file("imagen-huerfana.jpg"),
                },
            )

        self.assertFalse(self.business.public_images.exists())
        self.assertEqual(
            [path for path in Path(self.media_directory.name).rglob("*") if path.is_file()],
            [],
        )

    def test_professional_cannot_select_an_image_from_another_business(self):
        foreign_image = BusinessPublicImage.objects.create(
            business=self.other_business,
            image=self._image_file("privada-norte.jpg"),
            label="Privada Norte",
        )
        self.client.force_login(self.professional)

        response = self.client.post(
            self.url,
            {
                "professional_theme": Business.ProfessionalTheme.LIGHT,
                "public_image_choice": f"custom:{foreign_image.pk}",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selecciona una imagen disponible.")
        self.assertFalse(foreign_image.is_selected)

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

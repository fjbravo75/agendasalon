from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from PIL import Image

from apps.businesses.forms import BusinessVisualSettingsForm
from apps.businesses.models import Business, BusinessPublicImage


@skipUnlessDBFeature("has_select_for_update")
class PostgreSQLBusinessImageQuotaConcurrencyTests(TransactionTestCase):
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
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
            is_active=True,
        )
        for index in range(11):
            BusinessPublicImage.objects.create(
                business=self.business,
                image=SimpleUploadedFile(
                    f"existente-{index}.webp",
                    b"imagen-existente",
                    content_type="image/webp",
                ),
                label=f"Existente {index}",
                is_selected=index == 10,
                uploaded_by=self.professional,
            )

    @staticmethod
    def _valid_upload(filename):
        output = BytesIO()
        Image.new("RGB", (800, 500), color=(142, 111, 93)).save(output, format="PNG")
        return SimpleUploadedFile(filename, output.getvalue(), content_type="image/png")

    def test_two_uploads_competing_for_the_last_slot_persist_only_one(self):
        def upload(filename):
            connections.close_all()
            try:
                business = Business.objects.get(pk=self.business.pk)
                professional = get_user_model().objects.get(pk=self.professional.pk)
                form = BusinessVisualSettingsForm(
                    {"professional_theme": Business.ProfessionalTheme.LIGHT},
                    {"new_public_image": self._valid_upload(filename)},
                    instance=business,
                )
                if not form.is_valid():
                    return False
                try:
                    form.save(uploaded_by=professional)
                except ValidationError:
                    return False
                return True
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(upload, ("candidata-a.png", "candidata-b.png")))

        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(outcomes.count(False), 1)
        self.assertEqual(self.business.public_images.count(), 12)

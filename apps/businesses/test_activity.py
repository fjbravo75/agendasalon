from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.businesses.activity import record_business_activity
from apps.businesses.models import Business, BusinessActivityEvent


class BusinessActivityTests(TestCase):
    def setUp(self):
        self.business = Business.objects.create(
            commercial_name="Peluquería Mari",
            slug="peluqueria-mari",
        )
        self.superadmin = get_user_model().objects.create_superuser(
            normalized_phone="+34910000001",
            password="test-pass-123",
            full_name="Admin AgendaSalon",
        )

    def test_record_activity_infers_actor_and_updates_business_timestamp(self):
        event = record_business_activity(
            business=self.business,
            category=BusinessActivityEvent.Category.PLATFORM,
            event_type=BusinessActivityEvent.EventType.BUSINESS_UPDATED,
            origin=BusinessActivityEvent.Origin.PLATFORM,
            summary="Datos del negocio actualizados.",
            actor=self.superadmin,
            entity=self.business,
            entity_type="business",
        )

        self.business.refresh_from_db()
        self.assertEqual(event.actor_type, BusinessActivityEvent.ActorType.SUPERADMIN)
        self.assertEqual(event.actor_label, "Admin AgendaSalon")
        self.assertEqual(event.entity_id, self.business.id)
        self.assertEqual(self.business.last_activity_at, event.created_at)

    def test_empty_activity_summary_is_rejected(self):
        with self.assertRaises(ValidationError):
            record_business_activity(
                business=self.business,
                category=BusinessActivityEvent.Category.PLATFORM,
                event_type=BusinessActivityEvent.EventType.BUSINESS_UPDATED,
                origin=BusinessActivityEvent.Origin.SYSTEM,
                summary="   ",
            )

        self.assertFalse(BusinessActivityEvent.objects.exists())

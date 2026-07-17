from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from apps.legal.models import (
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    DataRightsRequest,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)


class LegalAdminReadonlyTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/legal/")
        self.request.user = get_user_model().objects.create_superuser(
            normalized_phone="+34600999171",
            phone="+34600999171",
            password="test-pass-123",
            full_name="Superadministración Legal",
        )

    def test_documents_and_evidence_are_readonly_at_runtime(self):
        for model in (
            LegalDocument,
            LegalAcceptance,
            LegalAcceptanceEvent,
            CustomerPrivacyEvidence,
            CustomerPrivacyEvidenceEvent,
        ):
            with self.subTest(model=model._meta.label):
                model_admin = admin.site._registry[model]
                self.assertFalse(model_admin.has_add_permission(self.request))
                self.assertFalse(model_admin.has_change_permission(self.request))
                self.assertFalse(model_admin.has_delete_permission(self.request))

    def test_rights_requests_allow_follow_up_but_not_creation_or_deletion(self):
        model_admin = admin.site._registry[DataRightsRequest]

        self.assertFalse(model_admin.has_add_permission(self.request))
        self.assertTrue(model_admin.has_change_permission(self.request))
        self.assertFalse(model_admin.has_delete_permission(self.request))
        self.assertEqual(
            set(model_admin.get_form(self.request).base_fields),
            {"status", "resolution_note"},
        )

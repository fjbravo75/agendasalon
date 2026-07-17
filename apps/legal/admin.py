from django.contrib import admin
from django.db import transaction

from apps.legal.models import (
    BusinessLegalProfile,
    CustomerPrivacyEvidence,
    CustomerPrivacyEvidenceEvent,
    DataRightsRequest,
    LegalAcceptance,
    LegalAcceptanceEvent,
    LegalDocument,
)


@admin.register(LegalDocument)
class LegalDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "version", "kind", "is_active", "published_at")
    list_filter = ("kind", "is_active")
    search_fields = ("title", "slug", "version")
    readonly_fields = ("content_hash", "published_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BusinessLegalProfile)
class BusinessLegalProfileAdmin(admin.ModelAdmin):
    list_display = ("business", "legal_name", "privacy_email", "updated_at")
    search_fields = ("business__commercial_name", "legal_name", "tax_identifier")
    autocomplete_fields = ("business",)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        obj.business.__class__.objects.select_for_update().get(pk=obj.business_id)
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LegalAcceptance)
class LegalAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("document", "business", "action", "context", "accepted_at")
    list_filter = ("document__kind", "action", "context", "business")
    search_fields = (
        "business__commercial_name",
        "actor_user__full_name",
        "client_access__business_client__full_name",
    )
    readonly_fields = (
        "document",
        "business",
        "actor_user",
        "client_access",
        "action",
        "context",
        "document_hash_snapshot",
        "legal_context_snapshot",
        "authority_declared",
        "accepted_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LegalAcceptanceEvent)
class LegalAcceptanceEventAdmin(admin.ModelAdmin):
    list_display = ("document", "business", "action", "context", "accepted_at")
    list_filter = ("document__kind", "action", "context", "business")
    search_fields = (
        "business__commercial_name",
        "actor_user__full_name",
        "client_access__business_client__full_name",
    )
    readonly_fields = (
        "document",
        "business",
        "actor_user",
        "client_access",
        "action",
        "context",
        "document_hash_snapshot",
        "legal_context_snapshot",
        "authority_declared",
        "accepted_at",
        "recorded_at",
        "action_fingerprint",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DataRightsRequest)
class DataRightsRequestAdmin(admin.ModelAdmin):
    list_display = ("client_access", "business", "request_type", "status", "created_at")
    list_filter = ("request_type", "status", "business")
    search_fields = ("client_access__business_client__full_name", "detail")
    readonly_fields = (
        "business",
        "client_access",
        "request_type",
        "detail",
        "created_at",
        "updated_at",
        "resolved_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CustomerPrivacyEvidence)
class CustomerPrivacyEvidenceAdmin(admin.ModelAdmin):
    list_display = (
        "business_client",
        "informed_party_name_snapshot",
        "business",
        "event_type",
        "channel",
        "document",
        "occurred_at",
    )
    list_filter = ("event_type", "channel", "document__version", "business")
    search_fields = ("business_client__full_name", "business__commercial_name")
    readonly_fields = (
        "document",
        "business",
        "business_client",
        "client_access",
        "recorded_by",
        "event_type",
        "channel",
        "informed_party_type",
        "informed_party_name_snapshot",
        "document_hash_snapshot",
        "legal_context_snapshot",
        "occurred_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CustomerPrivacyEvidenceEvent)
class CustomerPrivacyEvidenceEventAdmin(admin.ModelAdmin):
    list_display = (
        "business_client",
        "informed_party_name_snapshot",
        "business",
        "event_type",
        "channel",
        "document",
        "occurred_at",
    )
    list_filter = ("event_type", "channel", "document__version", "business")
    search_fields = ("business_client__full_name", "business__commercial_name")
    readonly_fields = (
        "document",
        "business",
        "business_client",
        "client_access",
        "recorded_by",
        "event_type",
        "channel",
        "informed_party_type",
        "informed_party_name_snapshot",
        "document_hash_snapshot",
        "legal_context_snapshot",
        "occurred_at",
        "recorded_at",
        "action_fingerprint",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

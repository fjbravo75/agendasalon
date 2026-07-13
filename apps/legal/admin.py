from django.contrib import admin

from apps.legal.models import (
    BusinessLegalProfile,
    DataRightsRequest,
    LegalAcceptance,
    LegalDocument,
)


@admin.register(LegalDocument)
class LegalDocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "version", "kind", "is_active", "published_at")
    list_filter = ("kind", "is_active")
    search_fields = ("title", "slug", "version")
    readonly_fields = ("content_hash", "published_at")


@admin.register(BusinessLegalProfile)
class BusinessLegalProfileAdmin(admin.ModelAdmin):
    list_display = ("business", "legal_name", "privacy_email", "updated_at")
    search_fields = ("business__commercial_name", "legal_name", "tax_identifier")
    autocomplete_fields = ("business",)


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


@admin.register(DataRightsRequest)
class DataRightsRequestAdmin(admin.ModelAdmin):
    list_display = ("client_access", "business", "request_type", "status", "created_at")
    list_filter = ("request_type", "status", "business")
    search_fields = ("client_access__business_client__full_name", "detail")
    readonly_fields = ("business", "client_access", "request_type", "detail", "created_at")

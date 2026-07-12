from django.contrib import admin

from .models import (
    BusinessClient,
    BusinessClientAccess,
    BusinessClientAccessInvitation,
    BusinessClientAccessGrant,
    BusinessClientAuthorizedContact,
)


@admin.register(BusinessClientAccessGrant)
class BusinessClientAccessGrantAdmin(admin.ModelAdmin):
    list_display = (
        "business_client",
        "access",
        "relationship_label",
        "is_active",
        "updated_at",
    )
    list_filter = ("relationship_label", "is_active", "business")
    search_fields = (
        "business_client__full_name",
        "access__business_client__full_name",
        "access__phone",
    )
    autocomplete_fields = ("business", "access", "business_client", "authorized_contact")
    readonly_fields = ("created_at", "updated_at")


class BusinessClientAuthorizedContactInline(admin.TabularInline):
    model = BusinessClientAuthorizedContact
    fk_name = "business_client"
    extra = 0
    fields = (
        "full_name",
        "linked_business_client",
        "phone",
        "relationship_label",
        "is_primary_contact",
        "is_active",
    )


class BusinessClientAccessInline(admin.StackedInline):
    model = BusinessClientAccess
    extra = 0
    fields = (
        "phone",
        "phone_normalized",
        "is_active",
        "last_login_at",
        "created_at",
        "updated_at",
    )
    readonly_fields = ("phone_normalized", "last_login_at", "created_at", "updated_at")


@admin.register(BusinessClient)
class BusinessClientAdmin(admin.ModelAdmin):
    list_display = ("full_name", "business", "phone", "source", "is_active", "last_activity_at")
    list_filter = ("source", "is_active", "business")
    search_fields = (
        "full_name",
        "full_name_normalized",
        "phone",
        "phone_normalized",
        "email",
        "business__commercial_name",
    )
    autocomplete_fields = ("business",)
    readonly_fields = (
        "full_name_normalized",
        "phone_normalized",
        "created_at",
        "updated_at",
    )
    inlines = [BusinessClientAccessInline, BusinessClientAuthorizedContactInline]


@admin.register(BusinessClientAuthorizedContact)
class BusinessClientAuthorizedContactAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "business_client",
        "relationship_label",
        "is_primary_contact",
        "is_active",
    )
    list_filter = ("relationship_label", "is_primary_contact", "is_active", "business")
    search_fields = (
        "full_name",
        "phone",
        "phone_normalized",
        "business_client__full_name",
        "business__commercial_name",
    )
    autocomplete_fields = ("business", "business_client", "linked_business_client")
    readonly_fields = ("phone_normalized", "created_at", "updated_at")


@admin.register(BusinessClientAccess)
class BusinessClientAccessAdmin(admin.ModelAdmin):
    list_display = ("business_client", "business", "phone", "is_active", "last_login_at")
    list_filter = ("is_active", "business")
    search_fields = (
        "phone",
        "phone_normalized",
        "business_client__full_name",
        "business__commercial_name",
    )
    autocomplete_fields = ("business", "business_client")
    readonly_fields = ("phone_normalized", "last_login_at", "created_at", "updated_at")


@admin.register(BusinessClientAccessInvitation)
class BusinessClientAccessInvitationAdmin(admin.ModelAdmin):
    list_display = (
        "business_client",
        "business",
        "expires_at",
        "used_at",
        "revoked_at",
        "created_at",
    )
    list_filter = ("business", "used_at", "revoked_at")
    search_fields = ("business_client__full_name", "business__commercial_name")
    autocomplete_fields = ("business", "business_client", "created_by")
    readonly_fields = (
        "id",
        "token_digest",
        "expires_at",
        "used_at",
        "revoked_at",
        "created_by",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

# Register your models here.

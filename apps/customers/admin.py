from django.contrib import admin

from .models import BusinessClient, BusinessClientAuthorizedContact


class BusinessClientAuthorizedContactInline(admin.TabularInline):
    model = BusinessClientAuthorizedContact
    extra = 0
    fields = (
        "full_name",
        "phone",
        "relationship_label",
        "is_primary_contact",
        "is_active",
    )


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
    inlines = [BusinessClientAuthorizedContactInline]


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
    autocomplete_fields = ("business", "business_client")
    readonly_fields = ("phone_normalized", "created_at", "updated_at")

# Register your models here.

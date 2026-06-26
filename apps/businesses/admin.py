from django.contrib import admin

from .models import Business, BusinessMembership


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("commercial_name", "slug", "city", "is_active", "created_at")
    list_filter = ("is_active", "city", "province")
    search_fields = ("commercial_name", "slug", "public_phone", "public_email")
    prepopulated_fields = {"slug": ("commercial_name",)}
    readonly_fields = ("created_at", "updated_at")


@admin.register(BusinessMembership)
class BusinessMembershipAdmin(admin.ModelAdmin):
    list_display = ("business", "user", "role", "is_active", "created_at")
    list_filter = ("role", "is_active", "business")
    search_fields = (
        "business__commercial_name",
        "user__full_name",
        "user__normalized_phone",
    )
    autocomplete_fields = ("business", "user")
    readonly_fields = ("created_at", "updated_at")

# Register your models here.

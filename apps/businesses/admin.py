from django.contrib import admin

from .models import Business, BusinessActivityEvent, BusinessMembership, BusinessPublicImage


@admin.register(Business)
class BusinessAdmin(admin.ModelAdmin):
    list_display = ("commercial_name", "slug", "city", "is_active", "public_booking_enabled", "created_at")
    list_filter = ("is_active", "public_booking_enabled", "city", "province")
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


@admin.register(BusinessPublicImage)
class BusinessPublicImageAdmin(admin.ModelAdmin):
    list_display = ("label", "business", "is_selected", "uploaded_by", "created_at")
    list_filter = ("is_selected", "business")
    search_fields = ("label", "business__commercial_name")
    autocomplete_fields = ("business", "uploaded_by")
    readonly_fields = ("created_at",)


@admin.register(BusinessActivityEvent)
class BusinessActivityEventAdmin(admin.ModelAdmin):
    list_display = (
        "business",
        "event_type",
        "category",
        "actor_label",
        "origin",
        "created_at",
    )
    list_filter = ("category", "event_type", "origin", "business")
    search_fields = ("business__commercial_name", "actor_label", "summary")
    readonly_fields = (
        "business",
        "actor_user",
        "actor_type",
        "actor_label",
        "category",
        "event_type",
        "origin",
        "summary",
        "entity_type",
        "entity_id",
        "changes",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# Register your models here.

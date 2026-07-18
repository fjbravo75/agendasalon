from django.contrib import admin

from apps.core.models import DemoRefreshRequest, SecurityThrottle


@admin.register(SecurityThrottle)
class SecurityThrottleAdmin(admin.ModelAdmin):
    list_display = ("scope", "attempts", "blocked_until", "last_attempt_at")
    list_filter = ("scope",)
    search_fields = ("scope", "key_digest")
    readonly_fields = (
        "scope",
        "key_digest",
        "attempts",
        "window_started_at",
        "blocked_until",
        "last_attempt_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(DemoRefreshRequest)
class DemoRefreshRequestAdmin(admin.ModelAdmin):
    list_display = ("public_id", "status", "requested_by", "base_date", "requested_at")
    list_filter = ("status",)
    search_fields = ("public_id", "failure_code")
    readonly_fields = tuple(field.name for field in DemoRefreshRequest._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# Register your models here.

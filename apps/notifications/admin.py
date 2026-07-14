from django.contrib import admin

from .models import InternalNotification, OutboundEmail


@admin.register(InternalNotification)
class InternalNotificationAdmin(admin.ModelAdmin):
    list_display = ("event_type", "business", "channel", "status", "recipient_user", "created_at")
    list_filter = ("event_type", "channel", "status", "business")
    search_fields = (
        "content",
        "business__commercial_name",
        "business_client__full_name",
        "appointment__business_client__full_name",
    )
    autocomplete_fields = (
        "business",
        "business_client",
        "appointment",
        "recipient_user",
    )


@admin.register(OutboundEmail)
class OutboundEmailAdmin(admin.ModelAdmin):
    list_display = (
        "kind",
        "recipient_email",
        "business",
        "status",
        "scheduled_for",
        "attempts",
        "sent_at",
    )
    list_filter = ("kind", "status", "business")
    search_fields = ("recipient_email", "business__commercial_name", "deduplication_key")
    readonly_fields = (
        "kind",
        "business",
        "recipient_user",
        "client_access",
        "appointment",
        "recipient_email",
        "deduplication_key",
        "scheduled_for",
        "attempts",
        "sent_at",
        "last_error",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

# Register your models here.

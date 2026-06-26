from django.contrib import admin

from .models import InternalNotification


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

# Register your models here.

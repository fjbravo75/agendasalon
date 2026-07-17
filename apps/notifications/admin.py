from django.contrib import admin

from .models import InternalNotification, OutboundEmail


class OutboundEmailOperationalStatusFilter(admin.SimpleListFilter):
    title = "estado operativo"
    parameter_name = "operational_status"

    def lookups(self, request, model_admin):
        return [
            (
                value,
                (
                    "Aceptado por el servicio de correo"
                    if value == OutboundEmail.Status.SENT
                    else label
                ),
            )
            for value, label in OutboundEmail.Status.choices
        ]

    def queryset(self, request, queryset):
        value = self.value()
        if value in OutboundEmail.Status.values:
            return queryset.filter(status=value)
        return queryset


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
        "operational_status",
        "scheduled_for",
        "attempts",
        "lease_expires_at",
        "accepted_at",
    )
    list_filter = ("kind", OutboundEmailOperationalStatusFilter, "business")
    search_fields = ("recipient_email", "business__commercial_name", "deduplication_key")
    readonly_fields = (
        "kind",
        "business",
        "recipient_user",
        "client_access",
        "appointment",
        "recipient_email",
        "deduplication_key",
        "delivery_reference",
        "operational_status",
        "scheduled_for",
        "attempts",
        "lease_token",
        "lease_expires_at",
        "cancellation_requested_at",
        "accepted_at",
        "technical_last_error",
        "created_at",
        "updated_at",
    )
    fields = readonly_fields

    @admin.display(description="estado operativo", ordering="status")
    def operational_status(self, obj):
        return obj.operational_status_label

    @admin.display(description="aceptado por el servicio de correo el", ordering="sent_at")
    def accepted_at(self, obj):
        return obj.sent_at

    @admin.display(description="detalle técnico del último error")
    def technical_last_error(self, obj):
        return obj.last_error or "Sin detalle técnico registrado."

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# Register your models here.

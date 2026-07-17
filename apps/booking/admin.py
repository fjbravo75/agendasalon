from django.contrib import admin

from .models import (
    Appointment,
    AppointmentService,
    AvailabilityRule,
    BusinessCalendarSettings,
    BusinessClosure,
    Service,
    WorkLine,
)


class ReadOnlyOperationalAdminMixin:
    """Keep operational calendar data visible without bypassing domain services."""

    actions = None

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BusinessCalendarSettings)
class BusinessCalendarSettingsAdmin(ReadOnlyOperationalAdminMixin, admin.ModelAdmin):
    list_display = ("business", "slot_interval_minutes", "apply_national_holidays")
    autocomplete_fields = ("business",)


@admin.register(AvailabilityRule)
class AvailabilityRuleAdmin(ReadOnlyOperationalAdminMixin, admin.ModelAdmin):
    list_display = ("business", "weekday", "start_time", "end_time", "is_active")
    list_filter = ("weekday", "is_active", "business")
    autocomplete_fields = ("business",)


@admin.register(WorkLine)
class WorkLineAdmin(ReadOnlyOperationalAdminMixin, admin.ModelAdmin):
    list_display = ("business", "line_number", "name", "is_active", "display_order")
    list_filter = ("is_active", "business")
    search_fields = ("name", "business__commercial_name")
    autocomplete_fields = ("business",)


@admin.register(Service)
class ServiceAdmin(ReadOnlyOperationalAdminMixin, admin.ModelAdmin):
    list_display = (
        "name",
        "business",
        "duration_minutes",
        "price_amount",
        "is_active",
        "display_order",
    )
    list_filter = ("is_active", "business")
    search_fields = ("name", "description", "business__commercial_name")
    autocomplete_fields = ("business",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(BusinessClosure)
class BusinessClosureAdmin(ReadOnlyOperationalAdminMixin, admin.ModelAdmin):
    list_display = (
        "business",
        "work_line",
        "closure_type",
        "date_from",
        "date_to",
        "start_time",
        "end_time",
        "is_active",
    )
    list_filter = ("closure_type", "is_active", "business")
    search_fields = ("business__commercial_name", "internal_reason")
    autocomplete_fields = ("business", "work_line", "created_by")
    readonly_fields = ("created_at", "updated_at")


class AppointmentServiceInline(ReadOnlyOperationalAdminMixin, admin.TabularInline):
    model = AppointmentService
    extra = 0
    can_delete = False
    autocomplete_fields = ("service",)


@admin.register(Appointment)
class AppointmentAdmin(ReadOnlyOperationalAdminMixin, admin.ModelAdmin):
    list_display = (
        "business_client",
        "business",
        "work_line",
        "starts_at",
        "ends_at",
        "total_duration_minutes",
        "status",
        "manual_channel",
    )
    list_filter = ("status", "manual_channel", "business", "work_line")
    search_fields = (
        "business_client__full_name",
        "business_client__phone_normalized",
        "business__commercial_name",
        "service_summary_snapshot",
    )
    autocomplete_fields = (
        "business",
        "business_client",
        "work_line",
        "created_by",
        "requested_by_client_access",
        "cancelled_by",
        "completed_by",
        "no_show_marked_by",
    )
    readonly_fields = ("created_at", "updated_at", "no_show_marked_at")
    inlines = [AppointmentServiceInline]


@admin.register(AppointmentService)
class AppointmentServiceAdmin(ReadOnlyOperationalAdminMixin, admin.ModelAdmin):
    list_display = (
        "appointment",
        "service_name_snapshot",
        "duration_minutes_snapshot",
        "price_amount_snapshot",
        "display_order",
    )
    search_fields = (
        "service_name_snapshot",
        "appointment__business_client__full_name",
        "service__name",
    )
    autocomplete_fields = ("appointment", "service")

# Register your models here.

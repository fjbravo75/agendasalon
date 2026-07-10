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


@admin.register(BusinessCalendarSettings)
class BusinessCalendarSettingsAdmin(admin.ModelAdmin):
    list_display = ("business", "slot_interval_minutes", "apply_national_holidays")
    autocomplete_fields = ("business",)


@admin.register(AvailabilityRule)
class AvailabilityRuleAdmin(admin.ModelAdmin):
    list_display = ("business", "weekday", "start_time", "end_time", "is_active")
    list_filter = ("weekday", "is_active", "business")
    autocomplete_fields = ("business",)


@admin.register(WorkLine)
class WorkLineAdmin(admin.ModelAdmin):
    list_display = ("business", "line_number", "name", "is_active", "display_order")
    list_filter = ("is_active", "business")
    search_fields = ("name", "business__commercial_name")
    autocomplete_fields = ("business",)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
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
class BusinessClosureAdmin(admin.ModelAdmin):
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


class AppointmentServiceInline(admin.TabularInline):
    model = AppointmentService
    extra = 0
    autocomplete_fields = ("service",)


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
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
        "cancelled_by",
        "completed_by",
        "no_show_marked_by",
    )
    readonly_fields = ("created_at", "updated_at", "no_show_marked_at")
    inlines = [AppointmentServiceInline]


@admin.register(AppointmentService)
class AppointmentServiceAdmin(admin.ModelAdmin):
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

from django.contrib import admin
from django.utils import timezone

from .models import (
    HOLIDAY_SYNC_INTERRUPTED_AFTER,
    HolidaySyncRun,
    OfficialHoliday,
)


class HolidaySyncPresentationStatusFilter(admin.SimpleListFilter):
    title = "estado operativo"
    parameter_name = "presentation_status"

    def lookups(self, request, model_admin):
        return (
            ("running", "En curso"),
            ("interrupted", "Interrumpida"),
            (HolidaySyncRun.Status.SUCCESS, "Correcta"),
            (HolidaySyncRun.Status.FAILED, "Fallida"),
            (HolidaySyncRun.Status.PARTIAL, "Parcial"),
        )

    def queryset(self, request, queryset):
        selected = self.value()
        interruption_threshold = timezone.now() - HOLIDAY_SYNC_INTERRUPTED_AFTER
        if selected == "running":
            return queryset.filter(
                finished_at__isnull=True,
                started_at__gt=interruption_threshold,
            )
        if selected == "interrupted":
            return queryset.filter(
                finished_at__isnull=True,
                started_at__lte=interruption_threshold,
            )
        if selected in HolidaySyncRun.Status.values:
            return queryset.filter(
                finished_at__isnull=False,
                status=selected,
            )
        return queryset


class ReadOnlyHolidayAdminMixin:
    """Expose the official catalogue and its trace without allowing direct edits."""

    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OfficialHoliday)
class OfficialHolidayAdmin(ReadOnlyHolidayAdminMixin, admin.ModelAdmin):
    list_display = ("date", "name", "scope", "year", "source_name")
    list_filter = ("scope", "year", "source_name")
    search_fields = ("name", "source_name", "official_reference")


@admin.register(HolidaySyncRun)
class HolidaySyncRunAdmin(ReadOnlyHolidayAdminMixin, admin.ModelAdmin):
    list_display = (
        "year",
        "source_name",
        "visible_status",
        "items_loaded",
        "items_created",
        "items_updated",
        "items_removed",
        "affected_appointments",
        "started_at",
        "finished_at",
    )
    list_filter = (HolidaySyncPresentationStatusFilter, "year", "source_name")
    search_fields = ("source_name", "source_url", "error_detail")
    readonly_fields = (
        "year",
        "source_name",
        "source_url",
        "official_reference",
        "visible_status",
        "started_at",
        "finished_at",
        "items_loaded",
        "items_created",
        "items_updated",
        "items_removed",
        "items_skipped",
        "affected_appointments",
        "affected_businesses",
        "error_detail",
        "created_by",
    )
    fields = readonly_fields

    @admin.display(description="estado operativo", ordering="status")
    def visible_status(self, obj):
        if obj is None:
            return "—"
        return obj.presentation_status

# Register your models here.

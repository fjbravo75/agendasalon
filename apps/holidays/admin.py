from django.contrib import admin

from .models import HolidaySyncRun, OfficialHoliday


@admin.register(OfficialHoliday)
class OfficialHolidayAdmin(admin.ModelAdmin):
    list_display = ("date", "name", "scope", "year", "source_name")
    list_filter = ("scope", "year", "source_name")
    search_fields = ("name", "source_name", "official_reference")


@admin.register(HolidaySyncRun)
class HolidaySyncRunAdmin(admin.ModelAdmin):
    list_display = ("year", "source_name", "status", "items_loaded", "started_at", "finished_at")
    list_filter = ("status", "year", "source_name")
    search_fields = ("source_name", "source_url", "error_detail")
    autocomplete_fields = ("created_by",)

# Register your models here.

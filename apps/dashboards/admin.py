
from django.contrib import admin

from apps.dashboards.models import BackupExecution


@admin.register(BackupExecution)
class BackupExecutionAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "status",
        "destination",
        "integrity_verified",
        "authenticity_verified",
        "total_size_bytes",
    )
    list_filter = ("status", "destination")
    date_hierarchy = "started_at"
    readonly_fields = (
        "status",
        "destination",
        "started_at",
        "finished_at",
        "database_included",
        "media_included",
        "integrity_verified",
        "authenticity_verified",
        "total_size_bytes",
        "failure_code",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

from django.contrib import admin

from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = ["id", "status", "progress", "original_filename", "created_at", "completed_at"]
    list_filter = ["status"]
    search_fields = ["id", "original_filename", "prompt"]
    readonly_fields = [
        "id",
        "status",
        "progress",
        "file_path",
        "result_path",
        "generated_regex",
        "row_count",
        "error_message",
        "created_at",
        "updated_at",
        "started_at",
        "completed_at",
    ]
    ordering = ["-created_at"]

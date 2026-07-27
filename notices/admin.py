# notices/admin.py
from django.contrib import admin
from .models import Announcement, Excellence
from core.admin import SchoolScopedAdmin

@admin.register(Announcement)
class AnnouncementAdmin(SchoolScopedAdmin):
    list_display = ("title", "school", "level", "occasion_theme", "starts_at", "expires_at", "is_active", "active_now")
    list_filter = ("school", "level", "occasion_theme", "is_active")
    search_fields = ("title", "body")

from django.utils.html import format_html
@admin.register(Excellence)
class ExcellenceAdmin(SchoolScopedAdmin):
    list_display = ("teacher_name", "priority", "start_at", "end_at", "preview")
    fields = ("teacher_name", "reason", "photo", "photo_url", "start_at", "end_at", "priority")
    readonly_fields = ("preview",)

    def preview(self, obj):
        src = obj.image_src
        if not src:
            return "-"
        return format_html('<img src="{}" style="height:60px;border-radius:8px" />', src)
    preview.short_description = "معاينة"

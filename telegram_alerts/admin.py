from django.contrib import admin

from .models import TelegramAlert


@admin.register(TelegramAlert)
class TelegramAlertAdmin(admin.ModelAdmin):
    list_display = (
        "event_type",
        "status",
        "attempts",
        "created_at",
        "sent_at",
    )
    list_filter = ("status", "event_type", "created_at")
    search_fields = ("dedupe_key", "message", "last_error")
    readonly_fields = (
        "event_type",
        "dedupe_key",
        "message",
        "action_url",
        "action_label",
        "status",
        "attempts",
        "available_at",
        "locked_at",
        "sent_at",
        "telegram_message_id",
        "last_error",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False

from django.contrib import admin

from .models import MailMessage, MailWebhookEvent


@admin.register(MailMessage)
class MailMessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "direction", "status", "primary_recipient", "provider_created_at")
    list_filter = ("direction", "status", "is_read", "is_sensitive")
    search_fields = ("subject", "from_address", "provider_id")
    readonly_fields = ("local_reference", "provider_id", "created_at", "updated_at")


@admin.register(MailWebhookEvent)
class MailWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "event_id", "message", "occurred_at", "received_at")
    list_filter = ("event_type",)
    search_fields = ("event_id", "message__provider_id", "message__subject")
    readonly_fields = ("event_id", "event_type", "message", "occurred_at", "payload", "received_at")

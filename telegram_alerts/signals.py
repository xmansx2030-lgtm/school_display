from __future__ import annotations

import logging
from html import escape
from urllib.parse import urljoin

from django.conf import settings
from django.db.models.signals import post_save
from django.urls import reverse
from django.utils import timezone

logger = logging.getLogger(__name__)


def html_value(value: object) -> str:
    return escape(str(value or "—"), quote=False)


def admin_action_url(view_name: str, *, kwargs: dict | None = None) -> str:
    path = reverse(view_name, kwargs=kwargs)
    base = str(settings.TELEGRAM_ALERTS_BASE_URL).rstrip("/") + "/"
    return urljoin(base, path.lstrip("/"))


def _local_timestamp(value) -> str:
    if not value:
        return "—"
    try:
        return timezone.localtime(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(value)


def _queue_safely(**payload) -> None:
    from .services import alerts_enabled, queue_alert

    if not alerts_enabled():
        return
    try:
        queue_alert(**payload)
    except Exception:
        logger.exception(
            "telegram_alert_queue_failed event_type=%s dedupe_key=%s",
            payload.get("event_type"),
            payload.get("dedupe_key"),
        )


def _subscription_request_created(sender, instance, created: bool, **kwargs) -> None:
    if not created:
        return
    request_label = instance.get_request_type_display()
    message = (
        f"<b>🆕 {html_value(request_label)}</b>\n\n"
        f"🏫 المدرسة: <b>{html_value(instance.school)}</b>\n"
        f"📦 الباقة: {html_value(instance.plan)}\n"
        f"💳 المبلغ: <code>{html_value(instance.amount)} ر.س</code>\n"
        f"📅 تاريخ البدء المطلوب: <code>{instance.requested_starts_at.isoformat()}</code>\n"
        f"🕒 أُرسل في: <code>{_local_timestamp(instance.created_at)}</code>"
    )
    _queue_safely(
        event_type="subscription_request_created",
        dedupe_key=f"subscription-request-created:{instance.pk}",
        message=message,
        action_url=admin_action_url(
            "dashboard:system_subscription_request_detail",
            kwargs={"pk": instance.pk},
        ),
        action_label="مراجعة الطلب",
    )


def _subscription_created(sender, instance, created: bool, **kwargs) -> None:
    if not created:
        return
    end_date = instance.ends_at.isoformat() if instance.ends_at else "مفتوح المدة"
    message = (
        "<b>✅ تم إنشاء اشتراك جديد</b>\n\n"
        f"🏫 المدرسة: <b>{html_value(instance.school)}</b>\n"
        f"📦 الباقة: {html_value(instance.plan)}\n"
        f"📅 البداية: <code>{instance.starts_at.isoformat()}</code>\n"
        f"🏁 النهاية: <code>{html_value(end_date)}</code>\n"
        f"📌 الحالة: {html_value(instance.get_status_display())}"
    )
    _queue_safely(
        event_type="subscription_created",
        dedupe_key=f"subscription-created:{instance.pk}",
        message=message,
        action_url=admin_action_url(
            "dashboard:system_subscription_edit",
            kwargs={"pk": instance.pk},
        ),
        action_label="فتح الاشتراك",
    )


def _support_ticket_created(sender, instance, created: bool, **kwargs) -> None:
    if not created:
        return
    preview = " ".join(str(instance.message or "").split())
    if len(preview) > 220:
        preview = preview[:217].rstrip() + "..."
    message = (
        "<b>🎫 تذكرة دعم جديدة</b>\n\n"
        f"📌 الموضوع: <b>{html_value(instance.subject)}</b>\n"
        f"🏫 المدرسة: {html_value(instance.school)}\n"
        f"👤 المستخدم: {html_value(instance.user)}\n"
        f"🚦 الأولوية: {html_value(instance.get_priority_display())}\n"
        f"💬 الرسالة: {html_value(preview)}\n"
        f"🕒 أُنشئت في: <code>{_local_timestamp(instance.created_at)}</code>"
    )
    _queue_safely(
        event_type="support_ticket_created",
        dedupe_key=f"support-ticket-created:{instance.pk}",
        message=message,
        action_url=admin_action_url(
            "dashboard:system_support_ticket_detail",
            kwargs={"pk": instance.pk},
        ),
        action_label="فتح التذكرة",
    )


def connect_signals() -> None:
    from core.models import SupportTicket
    from subscriptions.models import SchoolSubscription, SubscriptionRequest

    post_save.connect(
        _subscription_request_created,
        sender=SubscriptionRequest,
        dispatch_uid="telegram_alerts_subscription_request_created",
    )
    post_save.connect(
        _subscription_created,
        sender=SchoolSubscription,
        dispatch_uid="telegram_alerts_subscription_created",
    )
    post_save.connect(
        _support_ticket_created,
        sender=SupportTicket,
        dispatch_uid="telegram_alerts_support_ticket_created",
    )

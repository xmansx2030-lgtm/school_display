from __future__ import annotations

import logging

from django.db.models import Q
from django.utils import timezone

from core.models import School
from .models import SchoolSubscription, SubscriptionPaymentOperation

logger = logging.getLogger(__name__)


def sync_school_active(school_id: int) -> None:
    """
    ✅ المصدر الوحيد لتفعيل/تعطيل المدرسة هو subscriptions.SchoolSubscription
    تحدّث School.is_active بناءً على وجود اشتراك ساري.
    """
    today = timezone.localdate()

    has_active = (
        SchoolSubscription.objects.filter(
            school_id=school_id,
            status="active",
            starts_at__lte=today,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=today))
        .exists()
    )

    # تحديث مباشر بدون تحميل كائن School كامل
    School.objects.filter(id=school_id).update(is_active=has_active)


def _safe_sync(sender, instance, **kwargs):
    try:
        if instance.school_id:
            sync_school_active(instance.school_id)
    except Exception:
        logger.exception("Failed to sync school.is_active for school_id=%s", getattr(instance, "school_id", None))


def _safe_sync_delete(sender, instance, **kwargs):
    try:
        if instance.school_id:
            sync_school_active(instance.school_id)
    except Exception:
        logger.exception("Failed to sync school.is_active after delete for school_id=%s", getattr(instance, "school_id", None))


def _safe_create_invoice(sender, instance: SubscriptionPaymentOperation, created: bool, **kwargs):
    if not created:
        return
    try:
        if not instance.amount or instance.amount <= 0:
            return
        if hasattr(instance, "invoice"):
            return

        from .invoicing import build_invoice_from_operation

        build_invoice_from_operation(instance)
    except Exception:
        logger.exception(
            "Failed to create invoice for payment operation id=%s",
            getattr(instance, "id", None),
        )


def connect_signals():
    from django.db.models.signals import post_save, post_delete

    post_save.connect(_safe_sync, sender=SchoolSubscription, dispatch_uid="subscriptions_sync_school_active_save")
    post_delete.connect(_safe_sync_delete, sender=SchoolSubscription, dispatch_uid="subscriptions_sync_school_active_delete")

    post_save.connect(
        _safe_create_invoice,
        sender=SubscriptionPaymentOperation,
        dispatch_uid="subscriptions_create_invoice_on_payment_operation_save",
    )

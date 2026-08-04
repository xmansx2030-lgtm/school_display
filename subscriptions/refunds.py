"""Refund handling.

Recording a refund is a billing fact; revoking access is a separate decision.
Keeping them distinct means a goodwill partial refund does not switch a
school's screens off by accident.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from .models import SubscriptionPaymentOperation, SubscriptionRefund


logger = logging.getLogger(__name__)


def refunded_total(operation: SubscriptionPaymentOperation) -> Decimal:
    """Sum of refunds against an operation, excluding failed attempts."""
    total = (
        SubscriptionRefund.objects.filter(operation=operation)
        .exclude(status="failed")
        .aggregate(total=Sum("amount"))
        .get("total")
    )
    return Decimal(str(total or "0"))


def refundable_amount(operation: SubscriptionPaymentOperation) -> Decimal:
    """How much of an operation can still be refunded."""
    remaining = Decimal(str(operation.amount or "0")) - refunded_total(operation)
    return remaining if remaining > 0 else Decimal("0")


@transaction.atomic
def record_refund(
    operation: SubscriptionPaymentOperation,
    *,
    amount: Decimal,
    reason: str = "other",
    status: str = "completed",
    notes: str = "",
    gateway_reference: str = "",
    revoke_access: bool = False,
    actor=None,
    request=None,
) -> SubscriptionRefund:
    """Record a refund and, when asked, revoke the access it paid for."""
    from .access import invalidate_school_subscription_cache
    from .audit import record as audit_record

    locked = SubscriptionPaymentOperation.objects.select_for_update().get(pk=operation.pk)
    subscription = locked.subscription

    refund = SubscriptionRefund(
        operation=locked,
        school=locked.school,
        subscription=subscription,
        amount=Decimal(str(amount)),
        reason=reason,
        status=status,
        notes=notes,
        gateway_reference=gateway_reference,
        revokes_access=bool(revoke_access),
        created_by=actor,
    )
    # full_clean enforces the "never refund more than was paid" rule.
    refund.full_clean(exclude=["school", "subscription", "operation", "created_by"])
    refund.save()

    if revoke_access and subscription is not None and subscription.status == "active":
        subscription.status = "cancelled"
        subscription.closure_reason = subscription.closure_reason or "technical"
        subscription.closure_notes = (
            f"{subscription.closure_notes}\nإلغاء مرتبط باسترداد بمبلغ {refund.amount} ر.س".strip()
        )
        subscription.save(
            update_fields=["status", "closure_reason", "closure_notes", "closed_at", "updated_at"]
        )
        invalidate_school_subscription_cache(locked.school_id)

    audit_record(
        "refund_completed" if status == "completed" else "refund_recorded",
        school=locked.school,
        subscription=subscription,
        actor=actor,
        request=request,
        amount=refund.amount,
        summary=(
            f"استرداد {refund.amount} ر.س - {refund.get_reason_display()}"
            + (" مع إيقاف الاشتراك" if revoke_access else "")
        ),
        context={
            "operation_id": locked.pk,
            "method": locked.method,
            "gateway_reference": gateway_reference,
            "revoked_access": bool(revoke_access),
            "remaining_refundable": str(refundable_amount(locked)),
        },
    )

    logger.info(
        "subscription_refund_recorded operation_id=%s amount=%s revoke=%s",
        locked.pk,
        refund.amount,
        revoke_access,
    )
    return refund

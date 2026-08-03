from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import MoyasarCheckout, SchoolSubscription, SubscriptionPaymentOperation


class MoyasarVerificationError(RuntimeError):
    pass


def amount_to_minor_units(value: Decimal | int | str) -> int:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise MoyasarVerificationError("مبلغ عملية الدفع غير صالح.") from exc
    return int(amount * 100)


def _payment_metadata(details: dict) -> dict:
    metadata = details.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _validate_details(checkout: MoyasarCheckout, details: dict) -> tuple[str, str]:
    payment_id = str(details.get("id") or "").strip()
    status = str(details.get("status") or "").strip().lower()
    if not payment_id or len(payment_id) > 80:
        raise MoyasarVerificationError("رقم دفعة ميسر غير صالح.")
    if checkout.payment_id and checkout.payment_id != payment_id:
        raise MoyasarVerificationError("دفعة ميسر لا تطابق الطلب المحلي.")

    amount = details.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise MoyasarVerificationError("تعذر التحقق من مبلغ دفعة ميسر.")
    if amount != amount_to_minor_units(checkout.amount):
        raise MoyasarVerificationError("مبلغ دفعة ميسر لا يطابق قيمة الاشتراك.")
    if str(details.get("currency") or "").upper() != checkout.currency:
        raise MoyasarVerificationError("عملة دفعة ميسر لا تطابق الطلب.")

    metadata = _payment_metadata(details)
    if str(metadata.get("merchant_reference") or "") != checkout.merchant_reference:
        raise MoyasarVerificationError("مرجع دفعة ميسر لا يطابق الطلب.")

    live_value = details.get("live")
    if not isinstance(live_value, bool) or live_value != checkout.live_mode:
        raise MoyasarVerificationError("بيئة دفعة ميسر لا تطابق إعدادات الطلب.")
    return payment_id, status


def _activate_paid_checkout(checkout: MoyasarCheckout, *, status: str, event_type: str) -> MoyasarCheckout:
    may_activate = checkout.live_mode or bool(getattr(settings, "MOYASAR_ACTIVATE_TEST_PAYMENTS", False))
    if may_activate:
        subscription, _created = SchoolSubscription.objects.get_or_create(
            school=checkout.school,
            plan=checkout.plan,
            starts_at=checkout.starts_at,
            defaults={
                "status": "active",
                "notes": f"Paid via Moyasar {checkout.merchant_reference}",
            },
        )
        if subscription.status != "active":
            subscription.status = "active"
            subscription.save(update_fields=["status", "updated_at"])

        operation = checkout.payment_operation
        if operation is None:
            operation = SubscriptionPaymentOperation.objects.create(
                school=checkout.school,
                subscription=subscription,
                plan=checkout.plan,
                amount=checkout.amount,
                method="moyasar",
                source="request",
                created_by=checkout.created_by,
                note=f"Moyasar {checkout.merchant_reference} / {checkout.payment_id}",
            )
        checkout.subscription = subscription
        checkout.payment_operation = operation

    checkout.status = status
    checkout.last_event = event_type[:40]
    checkout.processed_at = timezone.now()
    checkout.error_message = ""
    checkout.save(
        update_fields=[
            "subscription",
            "payment_operation",
            "status",
            "last_event",
            "processed_at",
            "error_message",
            "updated_at",
        ]
    )
    return checkout


def apply_payment_details(checkout_id: int, details: dict, *, event_type: str) -> MoyasarCheckout:
    """Verify amount, currency, reference and environment before changing access."""
    with transaction.atomic():
        checkout = (
            # Lock only the checkout row. ``payment_operation`` is nullable, so
            # PostgreSQL rejects a blanket FOR UPDATE across its outer join.
            MoyasarCheckout.objects.select_for_update(of=("self",))
            .select_related("school", "plan", "created_by", "payment_operation")
            .get(pk=checkout_id)
        )
        payment_id, remote_status = _validate_details(checkout, details)

        other_checkout = MoyasarCheckout.objects.exclude(pk=checkout.pk).filter(payment_id=payment_id).exists()
        if other_checkout:
            raise MoyasarVerificationError("دفعة ميسر مرتبطة بطلب آخر.")
        if checkout.payment_id != payment_id:
            checkout.payment_id = payment_id
            checkout.save(update_fields=["payment_id", "updated_at"])

        if remote_status in {"paid", "captured"}:
            if checkout.payment_operation_id and checkout.status in {"paid", "captured"}:
                return checkout
            return _activate_paid_checkout(checkout, status=remote_status, event_type=event_type)

        if remote_status in {"initiated", "authorized", "failed", "refunded", "voided"}:
            checkout.status = remote_status
            checkout.last_event = event_type[:40]
            checkout.processed_at = timezone.now() if remote_status != "initiated" else None
            checkout.error_message = ""
            checkout.save(
                update_fields=[
                    "status",
                    "last_event",
                    "processed_at",
                    "error_message",
                    "updated_at",
                ]
            )
            return checkout

        raise MoyasarVerificationError("حالة دفعة ميسر غير معروفة.")

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from .models import SubscriptionInvoice, SubscriptionPaymentOperation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SellerInfo:
    name: str
    vat: str
    cr: str
    address: str
    phone: str
    email: str


def _get_seller_info() -> SellerInfo:
    # يمكن ضبط هذه القيم من settings.py لاحقًا
    return SellerInfo(
        name=getattr(settings, "INVOICE_SELLER_NAME", "منصة شاشة العرض الذكية") or "منصة شاشة العرض الذكية",
        vat=getattr(settings, "INVOICE_SELLER_VAT", "") or "",
        cr=getattr(settings, "INVOICE_SELLER_CR", "") or "",
        address=getattr(settings, "INVOICE_SELLER_ADDRESS", "المالك: منصور محمد الغامدي") or "المالك: منصور محمد الغامدي",
        phone=getattr(settings, "INVOICE_SELLER_PHONE", "") or "",
        email=getattr(settings, "INVOICE_SELLER_EMAIL", "") or "",
    )


def _get_school_contact_info(school: Any, preferred_user: Optional[Any] = None) -> Tuple[str, str]:
    """Resolve buyer contact name/mobile for a school.

    Priority:
    1) preferred_user if it's a non-staff/non-superuser user associated with this school.
    2) Any non-staff/non-superuser profile linked to this school.
    3) Fallback to any linked profile.
    """

    contact_name = ""
    contact_mobile = ""

    try:
        school_id = getattr(school, "id", None)

        # Allow staff users (school managers) but never allow superusers.
        if preferred_user and not getattr(preferred_user, "is_superuser", False):
            try:
                profile = getattr(preferred_user, "profile", None)
            except Exception:
                profile = None

            if profile:
                try:
                    is_for_school = (
                        getattr(profile, "active_school_id", None) == school_id
                        or profile.schools.filter(id=school_id).exists()
                    )
                except Exception:
                    is_for_school = False

                if is_for_school:
                    contact_name = (
                        f"{getattr(preferred_user, 'first_name', '')} {getattr(preferred_user, 'last_name', '')}".strip()
                        or getattr(preferred_user, "username", "")
                        or ""
                    )
                    contact_mobile = (getattr(profile, "mobile", "") or "").strip()
                    return contact_name, contact_mobile

        profile = None
        try:
            profile = school.users.select_related("user").filter(user__is_superuser=False).order_by("-id").first()
        except Exception:
            profile = None

        if profile is None:
            try:
                profile = school.users.select_related("user").order_by("-id").first()
            except Exception:
                profile = None

        if profile:
            u = getattr(profile, "user", None)
            contact_name = (
                f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
                or getattr(u, "username", "")
                or ""
            )
            contact_mobile = (getattr(profile, "mobile", "") or "").strip()
    except Exception:
        pass

    return contact_name, contact_mobile


def build_invoice_from_operation(op: SubscriptionPaymentOperation) -> SubscriptionInvoice:
    """إنشاء فاتورة وربطها بعملية الدفع + حفظ نسخة HTML."""
    seller = _get_seller_info()
    plan = op.plan
    school = op.school
    subscription = op.subscription

    inv = SubscriptionInvoice(
        operation=op,
        school=school,
        subscription=subscription,
        plan=plan,
        amount=op.amount,
        payment_method=op.method,
        issued_at=timezone.now(),
        seller_name=seller.name,
        buyer_name=getattr(school, "name", "") or "",
    )
    inv.save()

    contact_name, contact_mobile = _get_school_contact_info(school, preferred_user=getattr(op, "created_by", None))

    html = render_to_string(
        "invoices/subscription_invoice.html",
        {
            "invoice": inv,
            "seller": seller,
            "school": school,
            "subscription": subscription,
            "plan": plan,
            "contact_name": contact_name,
            "contact_mobile": contact_mobile,
        },
    )
    inv.html_snapshot = html
    inv.save(update_fields=["html_snapshot"])

    # Queue delivery only after the immutable invoice snapshot is complete.
    # Delivery happens in the background worker and never blocks payment flow.
    from .email_notifications import queue_invoice_email

    queue_invoice_email(inv)
    return inv


def reconcile_missing_invoices(*, batch_size: int = 100) -> int:
    """Backfill paid operations that were committed without an invoice.

    The normal path creates the invoice synchronously from the payment-operation
    signal. This reconciliation closes short deployment or transient-error gaps.
    """
    limit = max(1, min(int(batch_size), 1000))
    operations = list(
        SubscriptionPaymentOperation.objects.filter(
            amount__gt=0,
            invoice__isnull=True,
        )
        .select_related("school", "subscription", "plan", "created_by")
        .order_by("id")[:limit]
    )

    created = 0
    for operation in operations:
        try:
            build_invoice_from_operation(operation)
            created += 1
        except Exception:
            # Another process may have reconciled the same operation meanwhile.
            if SubscriptionInvoice.objects.filter(operation=operation).exists():
                continue
            logger.exception(
                "Failed to reconcile invoice for payment operation id=%s",
                operation.id,
            )
    return created

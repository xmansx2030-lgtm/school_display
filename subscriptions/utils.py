from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from django.db.models import Sum

from .models import SchoolSubscription, SubscriptionScreenAddon


def school_has_active_subscription(school_id: int, on_date=None) -> bool:
    today = on_date or timezone.localdate()

    return (
        SchoolSubscription.objects.filter(
            school_id=school_id,
            status="active",
            starts_at__lte=today,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=today))
        .exists()
    )


def school_active_paid_subscription(school_id: int, on_date=None):
    """Return the running paid base subscription, if one exists.

    A free trial grants its bundled screen, but it must not be treated as a
    paid base subscription for purchasing screen add-ons.
    """
    if not school_id:
        return None

    today = on_date or timezone.localdate()
    return (
        SchoolSubscription.objects.filter(
            school_id=school_id,
            status="active",
            starts_at__lte=today,
            plan__price__gt=0,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=today))
        .select_related("plan")
        .order_by("-ends_at", "-starts_at", "-id")
        .first()
    )


def school_has_paid_active_subscription(school_id: int, on_date=None) -> bool:
    """Whether the school may buy add-ons for an already-paid base plan."""
    return school_active_paid_subscription(school_id, on_date=on_date) is not None


def school_effective_max_screens(school_id: int, on_date=None) -> int | None:
    """يرجع حد الشاشات الفعلي = حد الخطة + زيادات الشاشات المدفوعة.

    - None تعني غير محدود.
    - 0 تعني لا يوجد اشتراك نشط.
    """
    today = on_date or timezone.localdate()

    subs = (
        SchoolSubscription.objects.filter(
            school_id=school_id,
            status="active",
            starts_at__lte=today,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=today))
        .select_related("plan")
        .defer("plan__duration_days")
    )

    subs_list = list(subs)
    if not subs_list:
        return 0

    best: int = 0
    for sub in subs_list:
        plan = getattr(sub, "plan", None)
        base = getattr(plan, "max_screens", None) if plan else 0
        if base is None:
            return None

        try:
            base_i = int(base or 0)
        except Exception:
            base_i = 0

        extra = (
            SubscriptionScreenAddon.objects.filter(
                subscription=sub,
                status="paid",
                starts_at__lte=today,
            )
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=today))
            .aggregate(s=Sum("screens_added"))
            .get("s")
            or 0
        )

        try:
            extra_i = int(extra or 0)
        except Exception:
            extra_i = 0

        effective = base_i + extra_i
        if effective > best:
            best = effective

    return best

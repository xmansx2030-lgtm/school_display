"""Deterministic subscription expiry.

Nothing in the request path flips a subscription to ``expired`` when its end
date passes: the model only recomputes state when something happens to save it.
This module is the scheduled counterpart that keeps stored state, school
activation, screen limits and cached access answers all consistent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils import timezone

from .models import SchoolSubscription


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpiryResult:
    expired: int = 0
    schools_synced: int = 0
    screens_disabled: int = 0


def due_subscription_ids(on_date=None) -> list[int]:
    """Subscriptions still marked active whose end date has already passed."""
    today = on_date or timezone.localdate()
    return list(
        SchoolSubscription.objects.filter(
            status="active",
            ends_at__isnull=False,
            ends_at__lt=today,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )


def expire_due_subscriptions(*, dry_run: bool = False, on_date=None) -> ExpiryResult:
    from core.screen_limits import enforce_school_screen_limit

    from .access import invalidate_school_subscription_cache
    from .signals import sync_school_active

    subscription_ids = due_subscription_ids(on_date=on_date)
    if not subscription_ids:
        return ExpiryResult()

    school_ids = sorted(
        set(
            SchoolSubscription.objects.filter(pk__in=subscription_ids).values_list(
                "school_id",
                flat=True,
            )
        )
    )

    if dry_run:
        return ExpiryResult(
            expired=len(subscription_ids),
            schools_synced=len(school_ids),
        )

    now = timezone.now()
    expired = SchoolSubscription.objects.filter(pk__in=subscription_ids).update(
        status="expired",
        closed_at=now,
        updated_at=now,
    )

    _audit_expired(subscription_ids)

    screens_disabled = 0
    schools_synced = 0
    for school_id in school_ids:
        try:
            sync_school_active(school_id)
            invalidate_school_subscription_cache(school_id)
            screens_disabled += _enforce_limits(enforce_school_screen_limit, school_id)
            schools_synced += 1
        except Exception:
            logger.exception("subscription_expiry_school_sync_failed school_id=%s", school_id)

    logger.info(
        "subscription_expiry expired=%s schools=%s screens_disabled=%s",
        expired,
        schools_synced,
        screens_disabled,
    )
    return ExpiryResult(
        expired=expired,
        schools_synced=schools_synced,
        screens_disabled=screens_disabled,
    )


def _audit_expired(subscription_ids: list[int]) -> None:
    """Leave a trail for every subscription the scheduler closed."""
    from .audit import record

    for subscription in SchoolSubscription.objects.filter(pk__in=subscription_ids).select_related(
        "school",
        "plan",
    ):
        record(
            "subscription_expired",
            school=subscription.school,
            subscription=subscription,
            summary=f"انتهاء اشتراك {subscription.plan} تلقائياً",
            context={
                "plan": str(subscription.plan),
                "ends_at": subscription.ends_at.isoformat() if subscription.ends_at else "",
            },
        )


def _enforce_limits(enforce, school_id: int) -> int:
    """Apply the screen limit and report how many screens it switched off."""
    from core.models import DisplayScreen

    before = DisplayScreen.objects.filter(school_id=school_id, is_active=True).count()
    enforce(school_id)
    after = DisplayScreen.objects.filter(school_id=school_id, is_active=True).count()
    return max(0, before - after)

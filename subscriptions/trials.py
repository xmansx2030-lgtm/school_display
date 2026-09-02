"""Retiring a free trial the moment a paid plan takes over.

Buying a plan creates a *new* ``SchoolSubscription`` row rather than editing
the trial the customer is still inside, so a school that upgrades on day 3 of
its 14-day trial ends up owning two live rows. The trial then keeps counting
down on its own, and every rule that reads a single row — expiry reminders, the
dashboard banner, the churn report — speaks about a term the customer already
replaced.

This module closes that overlap at the source: the trial is trimmed so it ends
exactly where the paid cover begins, never a day earlier, so the customer never
loses access they still hold.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import SchoolSubscription, SubscriptionScreenAddon

logger = logging.getLogger(__name__)

CLOSURE_REASON = "upgraded"
CLOSURE_NOTE = "تمت الترقية إلى باقة مدفوعة قبل انتهاء التجربة المجانية."


@dataclass(frozen=True)
class TrialClosureResult:
    trimmed: int = 0
    expired: int = 0
    schools: int = 0


def _paid_cover_start(trial: SchoolSubscription):
    """The earliest day a paid plan takes over from this trial, if any.

    Only paid cover that starts on or before the trial's own end date counts.
    A paid plan starting after the trial lapses is a genuine gap, and the trial
    should be allowed to run — and to warn — right up to its real end.
    """
    paid = (
        SchoolSubscription.objects.filter(
            school_id=trial.school_id,
            status="active",
            plan__price__gt=0,
            starts_at__lte=trial.ends_at,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=trial.starts_at))
        .exclude(pk=trial.pk)
        .order_by("starts_at", "id")
        .values_list("starts_at", flat=True)
        .first()
    )
    return paid


def _open_trials(school_id: int | None):
    trials = SchoolSubscription.objects.filter(
        status="active",
        plan__price=0,
        ends_at__isnull=False,
    ).select_related("plan", "school")
    if school_id:
        trials = trials.filter(school_id=school_id)
    return trials.order_by("id")


def close_superseded_trials(
    school_id: int | None = None,
    *,
    on_date=None,
    dry_run: bool = False,
) -> TrialClosureResult:
    """Trim every free trial a paid plan has already taken over.

    Pass a ``school_id`` to settle one customer right after their payment, or
    omit it to sweep the whole table — the operation is idempotent either way,
    because a trial that has already been trimmed no longer overlaps anything.
    """
    today = on_date or timezone.localdate()
    trimmed = 0
    expired = 0
    touched_schools: set[int] = set()

    for trial in _open_trials(school_id):
        paid_start = _paid_cover_start(trial)
        if paid_start is None:
            continue

        # End the day before paid cover begins, so not a single covered day is
        # taken away — and never before the trial's own start date.
        new_end = max(trial.starts_at, paid_start - timedelta(days=1))
        if new_end >= trial.ends_at:
            continue

        if _would_void_paid_screens(trial, new_end):
            logger.info(
                "trial_closure_skipped_has_addons subscription_id=%s school_id=%s",
                trial.pk,
                trial.school_id,
            )
            continue

        closes_now = new_end < today
        trimmed += 1
        expired += int(closes_now)
        touched_schools.add(trial.school_id)
        if dry_run:
            continue

        _apply_closure(trial, new_end=new_end, closes_now=closes_now)

    if not dry_run:
        for touched in sorted(touched_schools):
            _resync_school(touched)

    if trimmed:
        logger.info(
            "trial_closure trimmed=%s expired=%s schools=%s dry_run=%s",
            trimmed,
            expired,
            len(touched_schools),
            int(dry_run),
        )
    return TrialClosureResult(trimmed=trimmed, expired=expired, schools=len(touched_schools))


def _would_void_paid_screens(trial: SchoolSubscription, new_end) -> bool:
    """Whether trimming to ``new_end`` cuts short screens the customer bought.

    An add-on's screens only count while the subscription carrying them is
    running, so ending the trial early ends any add-on that would have outlived
    it. That is worth refusing — but only when money actually changed hands.

    The trial's own bundled screen is recorded as an add-on too, priced at
    zero, and treating that as untouchable was too blunt: it left the trial to
    lapse on its own, with no closure reason, so a school that had upgraded and
    was paying for a year landed in the churn report as a lost customer.
    """
    return (
        SubscriptionScreenAddon.objects.filter(
            subscription=trial,
            status="paid",
            total_price__gt=0,
        )
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=new_end))
        .exists()
    )


def _apply_closure(trial: SchoolSubscription, *, new_end, closes_now: bool) -> None:
    from .audit import record

    previous_end = trial.ends_at
    with transaction.atomic():
        trial.ends_at = new_end
        trial.closure_reason = CLOSURE_REASON
        trial.closure_notes = CLOSURE_NOTE
        fields = ["ends_at", "closure_reason", "closure_notes", "updated_at"]
        if closes_now:
            trial.status = "expired"
            fields.append("status")
        trial.save(update_fields=fields)

    record(
        "subscription_expired" if closes_now else "subscription_updated",
        school=trial.school,
        subscription=trial,
        summary=f"إنهاء التجربة المجانية {trial.plan} بعد الاشتراك في باقة مدفوعة",
        context={
            "plan": str(trial.plan),
            "previous_ends_at": previous_end.isoformat() if previous_end else "",
            "ends_at": new_end.isoformat(),
            "closed": closes_now,
        },
    )


def _resync_school(school_id: int) -> None:
    """Keep school activation and the cached access answer consistent."""
    from .access import invalidate_school_subscription_cache
    from .signals import sync_school_active

    try:
        sync_school_active(school_id)
        invalidate_school_subscription_cache(school_id)
    except Exception:
        logger.exception("trial_closure_school_sync_failed school_id=%s", school_id)


def close_superseded_trials_quietly(school_id: int | None) -> None:
    """Fire-and-forget variant for payment paths.

    Tidying the trial is bookkeeping; it must never be the reason a customer's
    completed payment fails to activate.
    """
    if not school_id:
        return
    try:
        close_superseded_trials(school_id)
    except Exception:
        logger.exception("trial_closure_failed school_id=%s", school_id)

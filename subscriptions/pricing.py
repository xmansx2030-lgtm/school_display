"""Single source of truth for what a purchase costs.

Both the admin-created ``SubscriptionScreenAddon`` and the customer-facing
checkout must arrive at the same figure. Keeping the arithmetic here means a
price change is one config edit, and the gateway can never charge an amount the
subscription record would disagree with.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings


# Screens a school may add in one purchase. The upper bound is a guard against
# a tampered form field turning into a five-figure charge.
MAX_EXTRA_SCREENS = 50


def monthly_screen_price() -> Decimal:
    return Decimal(str(getattr(settings, "SCREEN_ADDON_MONTHLY_PRICE", 60)))


def semiannual_multiplier() -> Decimal:
    return Decimal(str(getattr(settings, "SCREEN_ADDON_SEMIANNUAL_MULTIPLIER", 6)))


def annual_multiplier() -> Decimal:
    return Decimal(str(getattr(settings, "SCREEN_ADDON_ANNUAL_MULTIPLIER", 10)))


def cycle_multiplier_for_days(duration_days: int | None) -> Decimal:
    """Billing multiplier implied by a plan's length.

    Annual bills 10 months and semiannual 6, so longer commitments carry the
    same discount on extra screens as on the plan itself.
    """
    days = int(duration_days or 0)
    if days >= 330:
        return annual_multiplier()
    if days >= 150:
        return semiannual_multiplier()
    if days >= 80:
        return Decimal("3")
    return Decimal("1")


def screen_addon_price(screens: int, *, duration_days: int | None) -> Decimal:
    """Cost of ``screens`` extra screens for one plan term."""
    count = max(0, int(screens or 0))
    if count == 0:
        return Decimal("0.00")
    total = monthly_screen_price() * Decimal(count) * cycle_multiplier_for_days(duration_days)
    return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def normalize_extra_screens(value) -> int:
    """Coerce untrusted form input into a screen count we are willing to bill."""
    try:
        count = int(str(value or "0").strip() or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_EXTRA_SCREENS, count))


def prorated_screen_addon_price(screens: int, *, plan, starts_at, ends_at) -> Decimal:
    """Cost of extra screens bought part-way through an existing term.

    A school adding a screen in month ten of an annual plan pays for the
    remaining days only, keeping the same per-cycle discount as a full term.
    """
    full_term = screen_addon_price(screens, duration_days=getattr(plan, "duration_days", None))
    if full_term <= 0 or ends_at is None:
        return full_term

    remaining_days = (ends_at - starts_at).days + 1
    if remaining_days <= 0:
        return Decimal("0.00")

    term_days = int(getattr(plan, "duration_days", 0) or 0)
    if term_days <= 0:
        return full_term
    if remaining_days >= term_days:
        return full_term

    prorated = full_term * Decimal(remaining_days) / Decimal(term_days)
    return prorated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def checkout_total(plan, extra_screens: int = 0) -> Decimal:
    """Full amount to charge for a plan plus any extra screens."""
    base = Decimal(str(getattr(plan, "price", 0) or 0))
    addon = screen_addon_price(
        extra_screens,
        duration_days=getattr(plan, "duration_days", None),
    )
    return (base + addon).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

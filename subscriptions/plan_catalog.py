from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def _duration_details(duration_days: int | None) -> tuple[str, str, int | None]:
    days = int(duration_days or 0)
    if days <= 0:
        return "مدة مفتوحة", "للمدة كاملة", None
    if 27 <= days <= 31:
        return "شهر واحد", "شهرياً", 1
    if 80 <= days <= 100:
        return "3 أشهر", "كل 3 أشهر", 3
    if 150 <= days <= 200:
        return "6 أشهر", "نصف سنوي", 6
    if 330 <= days <= 370:
        return "سنة كاملة", "سنوياً", 12
    return f"{days} يوماً", f"لمدة {days} يوماً", None


def plan_card(plan) -> dict:
    """Return the single public/customer representation of a subscription plan."""
    duration_days = int(getattr(plan, "duration_days", 0) or 0)
    duration_label, period_label, cycle_months = _duration_details(duration_days)
    price = Decimal(str(getattr(plan, "price", 0) or 0))
    monthly_equivalent = None
    if price > 0 and cycle_months and cycle_months > 1:
        monthly_equivalent = (price / Decimal(cycle_months)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

    code = str(getattr(plan, "code", "") or "")
    name = str(getattr(plan, "name", "") or "")
    is_trial = code == "free-trial" or (price == 0 and "تجرب" in name)
    max_users = getattr(plan, "max_users", None)
    max_screens = getattr(plan, "max_screens", None)

    return {
        "id": getattr(plan, "pk", None),
        "code": code,
        "name": name,
        "description": str(getattr(plan, "description", "") or ""),
        "price": str(price),
        "duration_days": duration_days,
        "duration_label": duration_label,
        "period_label": period_label,
        "monthly_equivalent": str(monthly_equivalent) if monthly_equivalent is not None else None,
        "max_users": int(max_users) if max_users not in (None, "") else None,
        "max_screens": int(max_screens) if max_screens not in (None, "") else None,
        "is_featured": bool(getattr(plan, "is_featured", False)),
        "is_trial": is_trial,
        "is_free": price == 0,
    }


def plan_cards(plans) -> list[dict]:
    return [plan_card(plan) for plan in plans]

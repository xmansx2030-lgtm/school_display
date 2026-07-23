# schedule/time_engine.py
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Prefetch
from django.utils import timezone

from schedule.models import (
    Break,
    DEFAULT_DISPLAY_AFTER_BADGE,
    DEFAULT_DISPLAY_AFTER_HOLIDAY_BADGE,
    DEFAULT_DISPLAY_AFTER_HOLIDAY_TITLE,
    DEFAULT_DISPLAY_AFTER_TITLE,
    DEFAULT_DISPLAY_BEFORE_BADGE,
    DEFAULT_DISPLAY_BEFORE_TITLE,
    DEFAULT_DISPLAY_HOLIDAY_BADGE,
    DEFAULT_DISPLAY_HOLIDAY_TITLE,
    Period,
    WEEKDAYS,
)


WEEKDAY_LABELS = {value: label for value, label in WEEKDAYS}


def _resolve_display_messages(settings) -> dict[str, str]:
    getter = getattr(settings, "get_display_messages", None)
    if callable(getter):
        try:
            messages = getter() or {}
        except Exception:
            messages = {}
    else:
        messages = {}

    return {
        "display_before_title": (messages.get("before_title") or getattr(settings, "display_before_title", "") or DEFAULT_DISPLAY_BEFORE_TITLE).strip() or DEFAULT_DISPLAY_BEFORE_TITLE,
        "display_before_badge": (messages.get("before_badge") or getattr(settings, "display_before_badge", "") or DEFAULT_DISPLAY_BEFORE_BADGE).strip() or DEFAULT_DISPLAY_BEFORE_BADGE,
        "display_after_title": (messages.get("after_title") or getattr(settings, "display_after_title", "") or DEFAULT_DISPLAY_AFTER_TITLE).strip() or DEFAULT_DISPLAY_AFTER_TITLE,
        "display_after_badge": (messages.get("after_badge") or getattr(settings, "display_after_badge", "") or DEFAULT_DISPLAY_AFTER_BADGE).strip() or DEFAULT_DISPLAY_AFTER_BADGE,
        "display_after_holiday_title": (messages.get("after_holiday_title") or getattr(settings, "display_after_holiday_title", "") or DEFAULT_DISPLAY_AFTER_HOLIDAY_TITLE).strip() or DEFAULT_DISPLAY_AFTER_HOLIDAY_TITLE,
        "display_after_holiday_badge": (messages.get("after_holiday_badge") or getattr(settings, "display_after_holiday_badge", "") or DEFAULT_DISPLAY_AFTER_HOLIDAY_BADGE).strip() or DEFAULT_DISPLAY_AFTER_HOLIDAY_BADGE,
        "display_holiday_title": (messages.get("holiday_title") or getattr(settings, "display_holiday_title", "") or DEFAULT_DISPLAY_HOLIDAY_TITLE).strip() or DEFAULT_DISPLAY_HOLIDAY_TITLE,
        "display_holiday_badge": (messages.get("holiday_badge") or getattr(settings, "display_holiday_badge", "") or DEFAULT_DISPLAY_HOLIDAY_BADGE).strip() or DEFAULT_DISPLAY_HOLIDAY_BADGE,
    }


def _normalize_weekday_for_db(py_weekday: int) -> int:
    """
    Python: Monday=0 .. Sunday=6
    DB: Monday=1 .. Sunday=7
    """
    return py_weekday + 1


def _to_aware_dt(today, t, tz):
    dt = datetime.combine(today, t)
    return timezone.make_aware(dt, tz) if timezone.is_naive(dt) else dt


def _get_manager(obj, *names):
    for name in names:
        m = getattr(obj, name, None)
        if m is None:
            continue
        # RelatedManager has .all()
        if hasattr(m, "all"):
            return m
    return None


def _build_active_days_index(settings, *, weekdays: set[int] | None = None) -> dict[int, list]:
    day_qs = getattr(settings, "day_schedules", None)
    if day_qs is None or not hasattr(day_qs, "filter"):
        return {}

    try:
        day_qs = day_qs.filter(is_active=True)
        if weekdays:
            day_qs = day_qs.filter(weekday__in=sorted({int(day) for day in weekdays}))
        day_qs = (
            day_qs
            .only("id", "settings_id", "weekday", "is_active", "periods_count")
            .prefetch_related(
                Prefetch(
                    "periods",
                    queryset=(
                        Period.objects.select_related("subject", "teacher", "school_class")
                        .only(
                            "id",
                            "day_id",
                            "index",
                            "starts_at",
                            "ends_at",
                            "subject__id",
                            "subject__name",
                            "teacher__id",
                            "teacher__name",
                            "school_class__id",
                            "school_class__name",
                        )
                        .order_by("index", "starts_at")
                    ),
                ),
                Prefetch(
                    "breaks",
                    queryset=Break.objects.only("id", "day_id", "label", "starts_at", "duration_min").order_by("starts_at"),
                ),
            )
        )
    except Exception:
        day_qs = day_qs.filter(is_active=True)

    out: dict[int, list] = {}
    for day in day_qs:
        try:
            weekday = int(getattr(day, "weekday", 0) or 0)
        except Exception:
            continue
        if weekday == 0:
            weekday = 7
        if 1 <= weekday <= 7:
            out.setdefault(weekday, []).append(day)
    return out


def _active_weekdays(settings, active_days_index: dict[int, list] | None = None) -> set[int]:
    if active_days_index is not None:
        out: set[int] = set()
        for raw in active_days_index.keys():
            try:
                weekday = int(raw)
            except Exception:
                continue
            if 1 <= weekday <= 7:
                out.add(weekday)
        return out

    day_qs = getattr(settings, "day_schedules", None)
    if day_qs is None or not hasattr(day_qs, "filter"):
        return set()

    normalized = set()
    try:
        raw_values = day_qs.filter(is_active=True).values_list("weekday", flat=True)
    except Exception:
        raw_values = []

    for raw in raw_values:
        try:
            weekday = int(raw)
        except Exception:
            continue
        if weekday == 0:
            normalized.add(7)
        elif 1 <= weekday <= 7:
            normalized.add(weekday)
    return normalized


def _load_active_days_for_weekday(
    day_qs,
    weekday: int,
    weekday_legacy: int | None = None,
    *,
    active_days_index: dict[int, list] | None = None,
):
    if active_days_index is not None:
        days = list(active_days_index.get(weekday) or [])
        if not days and weekday_legacy is not None and weekday_legacy != weekday:
            legacy_days = list(active_days_index.get(weekday_legacy) or [])
            if legacy_days:
                return legacy_days, weekday_legacy
        return days, weekday

    if day_qs is None or not hasattr(day_qs, "filter"):
        return [], weekday

    days = list(day_qs.filter(weekday=weekday, is_active=True))
    if not days and weekday_legacy is not None and weekday_legacy != weekday:
        legacy_days = list(day_qs.filter(weekday=weekday_legacy, is_active=True))
        if legacy_days:
            return legacy_days, weekday_legacy

    return days, weekday


def _build_timeline_for_days(days, target_date, tz):
    timeline = []

    for day in days:
        prefetched = getattr(day, "_prefetched_objects_cache", {}) or {}
        prefetched_periods = prefetched.get("periods")
        periods_iter = None
        if prefetched_periods is not None:
            periods_iter = prefetched_periods
        else:
            periods_m = _get_manager(day, "periods", "period_set")
            if periods_m:
                periods_iter = periods_m.select_related("subject", "teacher", "school_class").only(
                    "id",
                    "day_id",
                    "index",
                    "starts_at",
                    "ends_at",
                    "subject__id",
                    "subject__name",
                    "teacher__id",
                    "teacher__name",
                    "school_class__id",
                    "school_class__name",
                ).all()

        if periods_iter is not None:
            for p in periods_iter:
                if not getattr(p, "starts_at", None) or not getattr(p, "ends_at", None):
                    continue

                start = _to_aware_dt(target_date, p.starts_at, tz)
                end = _to_aware_dt(target_date, p.ends_at, tz)
                if end < start:
                    continue

                timeline.append({
                    "kind": "period",
                    "index": getattr(p, "index", None),
                    "label": (p.subject.name if getattr(p, "subject", None) else "حصة"),
                    "class": (p.school_class.name if getattr(p, "school_class", None) else None),
                    "teacher": (p.teacher.name if getattr(p, "teacher", None) else None),
                    "start": start,
                    "end": end,
                })

        prefetched_breaks = prefetched.get("breaks")
        breaks_iter = None
        if prefetched_breaks is not None:
            breaks_iter = prefetched_breaks
        else:
            breaks_m = _get_manager(day, "breaks", "break_set")
            if breaks_m:
                breaks_iter = breaks_m.all()

        if breaks_iter is not None:
            for b in breaks_iter:
                if not getattr(b, "starts_at", None):
                    continue
                start = _to_aware_dt(target_date, b.starts_at, tz)
                dur = int(getattr(b, "duration_min", 0) or 0)
                if dur <= 0:
                    continue
                end = start + timedelta(minutes=dur)
                timeline.append({
                    "kind": "break",
                    "label": getattr(b, "label", None) or "استراحة",
                    "start": start,
                    "end": end,
                })

    return timeline


def _active_window_bounds(timeline):
    start_t = min(t["start"] for t in timeline)
    end_t = max(t["end"] for t in timeline)
    active_start = start_t - timedelta(minutes=30)
    active_end = end_t + timedelta(minutes=15)
    return start_t, end_t, active_start, active_end


def _next_school_day_info(settings, start_date, tz, *, include_today: bool = False, active_days_index: dict[int, list] | None = None):
    day_qs = getattr(settings, "day_schedules", None)
    if day_qs is None or not hasattr(day_qs, "filter"):
        return None

    active_weekdays = _active_weekdays(settings, active_days_index=active_days_index)
    if not active_weekdays:
        return None

    start_offset = 0 if include_today else 1
    for days_ahead in range(start_offset, 15):
        candidate = start_date + timedelta(days=days_ahead)
        weekday = _normalize_weekday_for_db(candidate.weekday())
        weekday_legacy = (candidate.weekday() + 1) % 7
        if weekday not in active_weekdays and weekday_legacy not in active_weekdays:
            continue

        days, resolved_weekday = _load_active_days_for_weekday(
            day_qs,
            weekday,
            weekday_legacy,
            active_days_index=active_days_index,
        )
        if not days:
            continue

        timeline = _build_timeline_for_days(days, candidate, tz)
        if not timeline:
            continue

        start_t, end_t, active_start, active_end = _active_window_bounds(timeline)
        return {
            "date": candidate.isoformat(),
            "weekday": resolved_weekday,
            "weekday_label": WEEKDAY_LABELS.get(resolved_weekday, ""),
            "days_ahead": days_ahead,
            "is_tomorrow": days_ahead == 1,
            "first_start": start_t.isoformat(),
            "last_end": end_t.isoformat(),
            "active_start": active_start.isoformat(),
            "active_end": active_end.isoformat(),
        }

    return None


def _after_hours_copy(settings_payload: dict[str, str], next_school_day):
    if next_school_day and int(next_school_day.get("days_ahead") or 0) == 1:
        return {
            "label": settings_payload["display_after_title"],
            "badge": settings_payload["display_after_badge"],
            "variant": "after_school_day",
        }

    return {
        "label": settings_payload["display_after_holiday_title"],
        "badge": settings_payload["display_after_holiday_badge"],
        "variant": "before_holiday",
    }


def build_day_snapshot(settings, now=None):
    """
    Snapshot contract for /api/display/snapshot

    returns:
      meta, settings, state, current_period, next_period, day_path,
      period_classes, standby{items}, excellence{items}
    """
    if now is None:
        now = timezone.localtime()

    # timezone
    tz_name = getattr(settings, "timezone_name", None) or "Asia/Riyadh"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Riyadh")

    now = timezone.localtime(now, tz)
    today = now.date()

    py_weekday = today.weekday()
    # DB: Monday=1 .. Sunday=7
    actual_weekday = _normalize_weekday_for_db(py_weekday)
    # Legacy deployments (old dashboard) used: Sunday=0 .. Saturday=6
    actual_weekday_legacy = (py_weekday + 1) % 7

    # theme mapping (لو عندك "default")
    raw_theme = (getattr(settings, "theme", None) or "indigo").strip().lower()
    if raw_theme in ("default", "dark", "light", ""):
        raw_theme = "indigo"

    # ✅ settings payload دائمًا كامل
    settings_payload = {
        "name": getattr(settings, "name", "") or "",
        "logo_url": getattr(settings, "logo_url", None),
        "theme": raw_theme,
        "timezone_name": tz_name,
        "refresh_interval_sec": int(getattr(settings, "refresh_interval_sec", 30) or 30),
        "standby_scroll_speed": float(getattr(settings, "standby_scroll_speed", 0.8) or 0.8),
        "periods_scroll_speed": float(getattr(settings, "periods_scroll_speed", 0.5) or 0.5),
    }
    settings_payload.update(_resolve_display_messages(settings))
    active_days_index = _build_active_days_index(settings)
    day_qs = getattr(settings, "day_schedules", None)

    actual_days, actual_resolved_weekday = _load_active_days_for_weekday(
        day_qs,
        actual_weekday,
        actual_weekday_legacy,
        active_days_index=active_days_index,
    )

    # Test mode is intended to show a chosen schedule on holidays only. If the
    # real current weekday has an active schedule, prefer it so a stale override
    # cannot keep the display in the wrong day.
    test_override = getattr(settings, "test_mode_weekday_override", None)
    if not actual_days and test_override is not None and 1 <= test_override <= 7:
        weekday = test_override
        weekday_legacy = None
    else:
        weekday = actual_resolved_weekday
        weekday_legacy = actual_weekday_legacy

    next_school_day = _next_school_day_info(
        settings,
        today,
        tz,
        include_today=False,
        active_days_index=active_days_index,
    )

    # الحصول على جدول اليوم (All Active Schedules for this weekday)
    if weekday == actual_resolved_weekday and weekday_legacy == actual_weekday_legacy:
        days = actual_days
    else:
        days, weekday = _load_active_days_for_weekday(
            day_qs,
            weekday,
            weekday_legacy,
            active_days_index=active_days_index,
        )

    if not days:
        # Optimization: Strict stop on holidays (reduced to 15m to allow updates/wake-up)
        settings_payload["refresh_interval_sec"] = 900
        return {
            "now": now.isoformat(),
            "meta": {
                "date": str(today),
                "weekday": weekday,
                "is_school_day": False,
                "is_active_window": False,
                "active_window": None,
                "next_school_day": next_school_day,
                "next_wake_at": (next_school_day or {}).get("active_start"),
            },
            "settings": settings_payload,
            "state": {
                "type": "off",
                "label": settings_payload["display_holiday_title"],
                "badge": settings_payload["display_holiday_badge"],
                "reason": "holiday",
                "from": None,
                "to": None,
                "remaining_seconds": None,
            },
            "current_period": None,
            "next_period": None,
            "day_path": [],
            "period_classes": [],
            "standby": {"items": []},
            "excellence": {"items": []},
        }

    timeline = _build_timeline_for_days(days, today, tz)

    if not timeline:
        # Optimization: Strict stop if empty timeline (reduced to 15m)
        settings_payload["refresh_interval_sec"] = 900
        return {
            "now": now.isoformat(),
            "meta": {
                "date": str(today),
                "weekday": weekday,
                "is_school_day": True,
                "is_active_window": False,
                "active_window": None,
                "next_school_day": next_school_day,
                "next_wake_at": (next_school_day or {}).get("active_start"),
            },
            "settings": settings_payload,
            "state": {
                "type": "off",
                "label": "لا يوجد مسار زمني لليوم",
                "badge": "تنبيه",
                "reason": "no_timeline",
                "from": None,
                "to": None,
                "remaining_seconds": None,
            },
            "current_period": None,
            "next_period": None,
            "day_path": [],
            "period_classes": [],
            "standby": {"items": []},
            "excellence": {"items": []},
        }

    # === Optimization: Strict Active Window & Smart Wakeup ===
    start_t, end_t, active_start, active_end = _active_window_bounds(timeline)
    
    # ✅ Strict Off-Hours Logic
    if now < active_start:
        # Before Window: Sleep / Smart Wakeup
        wait_seconds = (active_start - now).total_seconds()
        # Sleep until active_start, but poll every 15m max to catch schedule changes
        settings_payload["refresh_interval_sec"] = min(900, max(10, int(wait_seconds)))
        
        return {
            "now": now.isoformat(),
            "meta": {
                "date": str(today),
                "weekday": weekday,
                "is_school_day": True,
                "is_active_window": False,
                "active_window": {
                    "start": active_start.isoformat(),
                    "end": active_end.isoformat(),
                },
                "next_school_day": next_school_day,
                "next_wake_at": active_start.isoformat(),
            },
            "settings": settings_payload,
            "state": {
                "type": "off", 
                "label": settings_payload["display_before_title"], 
                "badge": settings_payload["display_before_badge"],
                "reason": "before_hours",
                "from": None, 
                "to": None, 
                "remaining_seconds": None
            },
            "current_period": None,
            "next_period": None,
            "day_path": [],
            "period_classes": [],
            "standby": {"items": []},
            "excellence": {"items": []},
        }

    elif now > active_end:
        # After Window: Sleep
        settings_payload["refresh_interval_sec"] = 900
        after_copy = _after_hours_copy(settings_payload, next_school_day)
        return {
            "now": now.isoformat(),
            "meta": {
                "date": str(today),
                "weekday": weekday,
                "is_school_day": True,
                "is_active_window": False,
                "active_window": {
                    "start": active_start.isoformat(),
                    "end": active_end.isoformat(),
                },
                "next_school_day": next_school_day,
                "next_wake_at": (next_school_day or {}).get("active_start"),
            },
            "settings": settings_payload,
            "state": {
                "type": "off", 
                "label": after_copy["label"], 
                "badge": after_copy["badge"],
                "reason": "after_hours",
                "variant": after_copy["variant"],
                "from": None, 
                "to": None, 
                "remaining_seconds": None
            },
            "current_period": None,
            "next_period": None,
            "day_path": [],
            "period_classes": [],
            "standby": {"items": []},
            "excellence": {"items": []},
        }

    # Within Active Window
    is_active_window = True
    active_window_meta = {

        "start": active_start.isoformat(),
        "end": active_end.isoformat(),
    }
    # ===================================

    timeline.sort(key=lambda x: x["start"])

    current = None
    next_item = None

    for i, block in enumerate(timeline):
        if block["start"] <= now < block["end"]:
            current = block
            next_item = timeline[i + 1] if i + 1 < len(timeline) else None
            break
        if now < block["start"]:
            next_item = block
            break

    def fmt(block):
        if not block:
            return None
        out = {
            "kind": block["kind"],
            "label": block.get("label"),
            "class": block.get("class"),
            "teacher": block.get("teacher"),
            "from": block["start"].strftime("%H:%M"),
            "to": block["end"].strftime("%H:%M"),
        }
        # ✅ important: keep period index so UI can show "حصة (رقم)" and
        # server-side merge can build period_classes without fragile time matching.
        if block.get("kind") == "period":
            out["index"] = block.get("index")
        return out

    day_path = []
    for b in timeline:
        block = {
            "kind": b["kind"],
            "label": b.get("label"),
            "from": b["start"].strftime("%H:%M"),
            "to": b["end"].strftime("%H:%M"),
        }
        if b.get("kind") == "period":
            # day_path powers the client-side transition engine, so it needs the
            # same metadata the hero chips depend on after local boundary changes.
            block["index"] = b.get("index")
            block["class"] = b.get("class")
            block["teacher"] = b.get("teacher")
        day_path.append(block)

    # state
    if current:
        state = {
            "type": current["kind"],
            "label": current.get("label"),
            "from": current["start"].strftime("%H:%M"),
            "to": current["end"].strftime("%H:%M"),
            "remaining_seconds": max(0, int((current["end"] - now).total_seconds())),
        }
        if current.get("kind") == "period":
            state["period_index"] = current.get("index")
    elif next_item:
        first = timeline[0]
        if now < first["start"]:
            state = {
                "type": "before",
                "label": settings_payload["display_before_title"],
                "badge": settings_payload["display_before_badge"],
                "reason": "before_hours",
                "from": first["start"].strftime("%H:%M"),
                "to": first["end"].strftime("%H:%M"),
                "remaining_seconds": max(0, int((first["start"] - now).total_seconds())),
            }
        else:
            state = {"type": "day", "label": "اليوم الدراسي", "from": None, "to": None, "remaining_seconds": None}
    else:
        # ✅ استخدم آخر نهاية فعلية (max end)
        last = max(timeline, key=lambda x: x["end"])
        after_copy = _after_hours_copy(settings_payload, next_school_day)
        state = {
            "type": "after",
            "label": after_copy["label"],
            "badge": after_copy["badge"],
            "reason": "after_hours",
            "variant": after_copy["variant"],
            "from": last["start"].strftime("%H:%M"),
            "to": last["end"].strftime("%H:%M"),
            "remaining_seconds": 0,
        }

    return {
        "now": now.isoformat(),
        "meta": {
            "date": str(today),
            "weekday": weekday,
            "is_school_day": True,
            "is_active_window": is_active_window,
            "active_window": active_window_meta,
            "next_school_day": next_school_day,
            "next_wake_at": None,
        },
        "settings": settings_payload,
        "state": state,
        "current_period": (fmt(current) | ({"remaining_seconds": max(0, int((current["end"] - now).total_seconds()))} if current else {})) if current else None,
        "next_period": fmt(next_item),
        "day_path": day_path,
        "period_classes": [],
        "standby": {"items": []},
        "excellence": {"items": []},
    }

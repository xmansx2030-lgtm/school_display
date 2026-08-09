"""
Wake broadcaster for display screens.

Computes today's active_start for a school and broadcasts a wake event
(reload) ahead of the school day so TVs that are sleeping have time to
re-render before the first period.

Used by the `display_wake_scheduler` management command.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from django.conf import settings as dj_settings
from django.core.cache import cache
from django.utils import timezone

from display.ws_groups import school_group_name
from schedule.closures import is_closed
from schedule.time_engine import (
    _active_window_bounds,
    _build_active_days_index,
    _build_timeline_for_days,
    _load_active_days_for_weekday,
    _normalize_weekday_for_db,
)

logger = logging.getLogger(__name__)

WAKE_SCHEDULER_HEARTBEAT_KEY = "display:wake_scheduler:heartbeat"
WAKE_SCHEDULER_HEARTBEAT_TTL_SECONDS = 180
_NO_ACTIVE_START = "none"


def _resolve_tz(school_settings) -> ZoneInfo:
    name = getattr(school_settings, "timezone_name", None) or "Asia/Riyadh"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Riyadh")


def compute_active_window_for_today(school_settings) -> Optional[tuple[datetime, datetime]]:
    """Return today's ``(active_start, active_end)`` or None on a non-school day.

    ``active_start`` is 30 minutes before the first period and ``active_end`` is
    15 minutes after the last one — the same bounds the display itself uses to
    decide whether it should be awake.
    """
    tz = _resolve_tz(school_settings)
    now = timezone.localtime(timezone.now(), tz)
    today = now.date()

    py_weekday = today.weekday()
    weekday = _normalize_weekday_for_db(py_weekday)
    weekday_legacy = (py_weekday + 1) % 7

    # إجازةٌ بتاريخها: اليوم له جدول أسبوعي كامل، لكنه ليس يوم دوام. وهذا ما
    # يجعل مراقب الانقطاع يصمت ومُوقظ الشاشات لا يوقظ أحداً.
    #
    # تُقدَّم على وضع الاختبار عمداً، خلافاً لمحرك العرض. الوضع موجود «لتشغيل
    # الشاشة في يوم إجازة للاختبار» — وهذا غرضٌ يخص ما **يُعرض** على الشاشة،
    # لا ما يوقظ أحداً في الرابعة فجراً. عَلَمُ اختبارٍ نسيه سوبر أدمن مفعّلاً
    # كان سيعيد التنبيهات طوال الإجازة، وهو بالضبط العطل الذي أُغلق هنا.
    if is_closed(school_settings, today):
        return None

    test_override = getattr(school_settings, "test_mode_weekday_override", None)
    if test_override is not None and 1 <= int(test_override) <= 7:
        weekday = int(test_override)

    day_qs = getattr(school_settings, "day_schedules", None)
    if day_qs is None or not hasattr(day_qs, "filter"):
        return None

    active_days_index = _build_active_days_index(
        school_settings,
        weekdays={int(weekday), int(weekday_legacy)},
    )
    days, _ = _load_active_days_for_weekday(
        day_qs, weekday, weekday_legacy, active_days_index=active_days_index,
    )
    if not days:
        return None

    timeline = _build_timeline_for_days(days, today, tz)
    if not timeline:
        return None

    _, _, active_start, active_end = _active_window_bounds(timeline)
    return active_start, active_end


def compute_active_start_for_today(school_settings) -> Optional[datetime]:
    """Return today's `active_start` (= first period start − 30min) or None.

    Returns None when there is no active timetable for today (holiday/weekend).
    """
    window = compute_active_window_for_today(school_settings)
    return window[0] if window else None


def _active_start_cache_key(school_settings, *, day_iso: str) -> str:
    school_id = int(getattr(school_settings, "school_id", 0) or 0)
    revision = int(getattr(school_settings, "schedule_revision", 0) or 0)
    override = int(getattr(school_settings, "test_mode_weekday_override", 0) or 0)
    return f"display:wake:active_start:{school_id}:{day_iso}:r{revision}:o{override}"


def _active_window_cache_key(school_settings, *, day_iso: str) -> str:
    school_id = int(getattr(school_settings, "school_id", 0) or 0)
    revision = int(getattr(school_settings, "schedule_revision", 0) or 0)
    override = int(getattr(school_settings, "test_mode_weekday_override", 0) or 0)
    return f"display:wake:active_window:{school_id}:{day_iso}:r{revision}:o{override}"


def cached_active_window_for_today(school_settings) -> Optional[tuple[datetime, datetime]]:
    """Cache today's active window by schedule revision.

    The offline monitor asks for this once per screen on every scan, so the
    timetable queries behind it must not be repeated per pass.
    """
    tz = _resolve_tz(school_settings)
    now = timezone.localtime(timezone.now(), tz)
    key = _active_window_cache_key(school_settings, day_iso=now.date().isoformat())

    try:
        cached = cache.get(key)
        if cached == _NO_ACTIVE_START:
            return None
        if cached is not None:
            start_ts, end_ts = cached
            return (
                datetime.fromtimestamp(float(start_ts), tz=tz),
                datetime.fromtimestamp(float(end_ts), tz=tz),
            )
    except Exception:
        pass

    window = compute_active_window_for_today(school_settings)
    next_day = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
    next_day = timezone.make_aware(next_day, tz) if timezone.is_naive(next_day) else next_day
    ttl = max(300, min(30 * 60 * 60, int((next_day - now).total_seconds()) + 2 * 60 * 60))
    try:
        cache.set(
            key,
            (window[0].timestamp(), window[1].timestamp()) if window is not None else _NO_ACTIVE_START,
            timeout=ttl,
        )
    except Exception:
        pass
    return window


def cached_active_start_for_today(school_settings) -> Optional[datetime]:
    """Cache today's wake calculation by schedule revision.

    The scheduler runs every minute, while a school's first-period boundary
    normally changes only when its schedule revision changes. This removes the
    repeated timetable queries without allowing stale schedules after edits.
    """
    tz = _resolve_tz(school_settings)
    now = timezone.localtime(timezone.now(), tz)
    key = _active_start_cache_key(school_settings, day_iso=now.date().isoformat())

    try:
        cached = cache.get(key)
        if cached == _NO_ACTIVE_START:
            return None
        if cached is not None:
            return datetime.fromtimestamp(float(cached), tz=tz)
    except Exception:
        pass

    active_start = compute_active_start_for_today(school_settings)
    next_day = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
    next_day = timezone.make_aware(next_day, tz) if timezone.is_naive(next_day) else next_day
    ttl = max(300, min(30 * 60 * 60, int((next_day - now).total_seconds()) + 2 * 60 * 60))
    try:
        cache.set(
            key,
            active_start.timestamp() if active_start is not None else _NO_ACTIVE_START,
            timeout=ttl,
        )
    except Exception:
        pass
    return active_start


def touch_wake_scheduler_heartbeat(*, worker_id: str) -> None:
    try:
        cache.set(
            WAKE_SCHEDULER_HEARTBEAT_KEY,
            {"ts": time.time(), "worker_id": str(worker_id or "wake-scheduler")},
            timeout=WAKE_SCHEDULER_HEARTBEAT_TTL_SECONDS,
        )
    except Exception:
        pass


def wake_scheduler_status() -> dict[str, object]:
    try:
        payload = cache.get(WAKE_SCHEDULER_HEARTBEAT_KEY)
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return {"alive": False, "age_sec": None, "worker_id": None}
    try:
        age_sec = max(0.0, time.time() - float(payload.get("ts") or 0.0))
    except (TypeError, ValueError):
        age_sec = None
    return {
        "alive": bool(age_sec is not None and age_sec <= WAKE_SCHEDULER_HEARTBEAT_TTL_SECONDS),
        "age_sec": round(age_sec, 3) if age_sec is not None else None,
        "worker_id": str(payload.get("worker_id") or "") or None,
    }


def broadcast_reload_to_school(school_id: int, *, reason: str = "wake_pre_active") -> bool:
    """Push a `reload` WS event to every screen in `school_id`'s group.

    Returns True on attempted send, False if WS layer is unavailable.
    """
    if not getattr(dj_settings, "DISPLAY_WS_ENABLED", False):
        return False

    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if not channel_layer:
            return False

        group_name = school_group_name(school_id)
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "broadcast_reload",
                "school_id": int(school_id),
                "reason": reason,
            },
        )
        logger.info(
            "wake_broadcast_sent school_id=%s group=%s reason=%s",
            school_id, group_name, reason,
        )
        return True
    except Exception as exc:
        logger.exception("wake_broadcast_failed school_id=%s: %s", school_id, exc)
        return False


def _dedup_key(school_id: int, day_iso: str, slot: str) -> str:
    return f"display:wake:fired:{int(school_id)}:{day_iso}:{slot}"


def maybe_fire_pre_active_wake(
    school_settings,
    *,
    lead_minutes: int = 30,
    window_seconds: int = 90,
) -> Optional[str]:
    """If now is inside the pre-active wake window, broadcast once per day.

    The pre-active wake fires `lead_minutes` before `active_start` and is
    deduplicated via Redis so each school is woken at most once per day per
    slot, even with multiple scheduler workers.

    Returns the slot label that fired, or None if nothing was sent.
    """
    school_id = int(getattr(school_settings, "school_id", 0) or 0)
    if school_id <= 0:
        return None

    active_start = cached_active_start_for_today(school_settings)
    if active_start is None:
        return None

    tz = _resolve_tz(school_settings)
    now = timezone.localtime(timezone.now(), tz)
    target = active_start - timedelta(minutes=int(lead_minutes))
    delta = (now - target).total_seconds()

    if abs(delta) > int(window_seconds):
        return None

    day_iso = active_start.date().isoformat()
    slot = f"pre_active_{int(lead_minutes)}m"
    key = _dedup_key(school_id, day_iso, slot)

    if not cache.add(key, "1", timeout=24 * 60 * 60):
        return None

    ok = broadcast_reload_to_school(school_id, reason=slot)
    if not ok:
        try:
            cache.delete(key)
        except Exception:
            pass
        return None
    return slot

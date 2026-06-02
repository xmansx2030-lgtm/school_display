from __future__ import annotations

import logging
import hashlib
from datetime import timedelta

from django.core.cache import cache
from django.db.models import F
from django.utils import timezone

from core.models import DisplayScreen
from display.cache_utils import keys as display_cache_keys
from display.cache_utils import normalize_day_key
from schedule.models import SchoolSettings


logger = logging.getLogger(__name__)

SNAPSHOT_CACHE_NAMESPACES = ("v10", "v11")


def status_metrics_day_key() -> str:
    try:
        return timezone.localdate().strftime("%Y%m%d")
    except Exception:
        return "00000000"


def status_metrics_key(*, day_key: str, name: str) -> str:
    return f"display:metrics:status:{day_key}:{name}"


def status_metrics_should_sample(*, token_hash: str, sample_every: int) -> bool:
    """Deterministic sampling: roughly 1/sample_every of requests.

    Using token_hash avoids per-process randomness skew and keeps it cheap.
    """
    try:
        n = int(sample_every or 0)
    except Exception:
        n = 0
    if n <= 1:
        return True
    try:
        # Use last 16 bits of sha256 to sample.
        return (int(str(token_hash)[-4:], 16) % int(n)) == 0
    except Exception:
        return False


def status_metrics_bump(*, day_key: str, name: str, ttl_sec: int = 86400) -> None:
    """Increment a daily counter in cache (best-effort).

    Works without DB. Safe for production (never raises).
    """
    try:
        ttl = int(ttl_sec or 0)
    except Exception:
        ttl = 86400
    ttl = max(60, min(86400 * 14, ttl))

    try:
        k = status_metrics_key(day_key=str(day_key), name=str(name))
        # Ensure it exists with TTL; some backends require key existence before incr.
        try:
            cache.add(k, 0, timeout=ttl)
        except Exception:
            pass
        try:
            cache.incr(k, 1)
        except Exception:
            v = cache.get(k) or 0
            cache.set(k, int(v) + 1, timeout=ttl)
    except Exception:
        return


def _school_rev_cache_key(school_id: int) -> str:
    return f"display:school_rev:{int(school_id)}"


def _school_rev_cache_ttl_seconds() -> int:
    # Long TTL; correctness is preserved because signals + refresh button update this value.
    # Even if cache is lost, DB fallback restores it.
    return 60 * 60 * 24 * 7  # 7 days


def get_cached_schedule_revision_for_school_id(school_id: int) -> int | None:
    if not school_id:
        return None
    try:
        v = cache.get(_school_rev_cache_key(int(school_id)))
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def set_cached_schedule_revision_for_school_id(school_id: int, rev: int) -> None:
    if not school_id:
        return
    try:
        cache.set(_school_rev_cache_key(int(school_id)), int(rev), timeout=_school_rev_cache_ttl_seconds())
    except Exception:
        pass


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def bump_schedule_revision_for_school_id(school_id: int) -> int | None:
    """Atomically increments schedule_revision for a school.

    Returns the new revision when possible.
    """
    if not school_id:
        return None

    # Use UPDATE to avoid triggering model signals recursively.
    try:
        SchoolSettings.objects.filter(school_id=int(school_id)).update(schedule_revision=F("schedule_revision") + 1)
    except Exception:
        return None

    try:
        new_rev = int(
            SchoolSettings.objects.filter(school_id=int(school_id)).values_list("schedule_revision", flat=True).first()
            or 0
        )
    except Exception:
        return None

    set_cached_schedule_revision_for_school_id(int(school_id), int(new_rev))
    return new_rev


REV_BUMP_WINDOW_SEC = 2  # Collapse rapid edit waves into a single bump.


def bump_schedule_revision_for_school_id_debounced(
    *,
    school_id: int,
    window_sec: int = REV_BUMP_WINDOW_SEC,
) -> bool:
    """Debounced revision bump.

    Returns True if a bump was performed, False if skipped due to debounce window.
    """

    school_id = int(school_id or 0)
    if not school_id:
        return False

    lock_key = f"display:rev_bump_window:{school_id}"
    try:
        acquired = bool(cache.add(lock_key, "1", timeout=int(window_sec)))
    except Exception:
        logger.exception("rev_bump_window cache error school_id=%s", school_id)
        # If cache is unavailable, prefer correctness over debouncing.
        acquired = True

    if not acquired:
        return False

    bump_schedule_revision_for_school_id(school_id)
    return True


def can_manual_refresh_school(school_id: int, *, window_sec: int = 5) -> bool:
    """Allow one manual refresh per short window.

    Returns True for the first refresh attempt within the window, False for repeats.
    Uses cache.add so it is atomic on Redis.
    """

    school_id = int(school_id or 0)
    if not school_id:
        return False

    key = f"display:manual_refresh_rl:{school_id}"
    try:
        return bool(cache.add(key, "1", timeout=int(window_sec)))
    except Exception:
        logger.exception("manual_refresh_rl cache error school_id=%s", school_id)
        # If cache is unavailable, do not block the user.
        return True


def get_schedule_revision_for_school_id(school_id: int) -> int | None:
    if not school_id:
        return None
    cached = get_cached_schedule_revision_for_school_id(int(school_id))
    if cached is not None:
        return int(cached)
    try:
        rev = int(
            SchoolSettings.objects.filter(school_id=int(school_id)).values_list("schedule_revision", flat=True).first()
            or 0
        )
        set_cached_schedule_revision_for_school_id(int(school_id), int(rev))
        return int(rev)
    except Exception:
        return None


def invalidate_display_snapshot_cache_for_school_id(school_id: int) -> None:
    """Best-effort cache invalidation for display snapshot.

    With revision-aware keys, invalidation is not strictly required for correctness,
    but it helps reclaim memory and avoids any edge cases where clients still carry
    old ETags.
    """

    if not school_id:
        return

    rev = get_schedule_revision_for_school_id(int(school_id)) or 0
    db_rev = 0
    try:
        db_rev = int(
            SchoolSettings.objects.filter(school_id=int(school_id)).values_list("schedule_revision", flat=True).first()
            or 0
        )
    except Exception:
        db_rev = 0

    revs = {rev, db_rev}
    for base_rev in (rev, db_rev):
        revs.add(max(0, int(base_rev or 0) - 1))
        revs.add(int(base_rev or 0) + 1)

    # Delete token-scoped snapshot caches, but keep token->school mapping.
    # The mapping stays valid across revision bumps and keeps /status cache-only.
    try:
        screens = DisplayScreen.objects.filter(school_id=int(school_id)).only("token")
    except Exception:
        screens = []

    for screen in screens:
        token_value = (getattr(screen, "token", "") or "").strip()
        if not token_value:
            continue
        token_hash = sha256(token_value)
        try:
            cache.delete(f"display:snapshot:{token_hash}")
        except Exception:
            pass
        for ns in SNAPSHOT_CACHE_NAMESPACES:
            try:
                cache.delete(f"display:snapshot:{ns}:{token_hash}")
            except Exception:
                pass
            try:
                cache.delete(f"display:snapshot:{ns}:{int(school_id)}:{token_hash}")
            except Exception:
                pass
        for r in revs:
            try:
                cache.delete(f"display:snapshot:{int(school_id)}:rev:{int(r)}:{token_hash}")
            except Exception:
                pass
            for ns in SNAPSHOT_CACHE_NAMESPACES:
                try:
                    cache.delete(f"display:snapshot:{ns}:{int(school_id)}:rev:{int(r)}:{token_hash}")
                except Exception:
                    pass

    # Delete a small window of possible school keys (today +/- 1) across current and previous revisions.
    try:
        today = timezone.localdate()
    except Exception:
        today = None

    dates = []
    if today:
        dates = [today, today - timedelta(days=1), today + timedelta(days=1)]

    day_candidates = set()
    for d in dates:
        try:
            day_candidates.add(d.isoformat())
        except Exception:
            pass
        try:
            day_candidates.add(d.strftime("%Y%m%d"))
        except Exception:
            pass
        try:
            day_candidates.add(normalize_day_key(d))
        except Exception:
            pass

    for r in revs:
        try:
            cache.delete(f"lock:snapshot:{int(school_id)}:{int(r)}")
        except Exception:
            pass
        for d in day_candidates:
            try:
                cache.delete(f"snapshot:v7:school:{int(school_id)}:rev:{int(r)}:day:{str(d)}")
            except Exception:
                pass
            try:
                cache.delete(f"snapshot:v8:school:{int(school_id)}:rev:{int(r)}:day:{str(d)}")
            except Exception:
                pass
            try:
                cache.delete(f"snapshot:v9:school:{int(school_id)}:day:{str(d)}")
            except Exception:
                pass
            for ns in SNAPSHOT_CACHE_NAMESPACES:
                try:
                    cache.delete(f"snapshot:{ns}:school:{int(school_id)}:day:{str(d)}")
                except Exception:
                    pass
                try:
                    cache.delete(f"snapshot:last:{ns}:{int(school_id)}:{str(d)}")
                except Exception:
                    pass
            try:
                cache.delete(f"lock:snapshot:{int(school_id)}:{int(r)}:day:{str(d)}")
            except Exception:
                pass
            try:
                cache.delete(f"lock:snapshot:{int(school_id)}:day:{str(d)}")
            except Exception:
                pass
            try:
                cache.delete(display_cache_keys.snapshot(int(school_id), int(r), str(d)))
            except Exception:
                pass
            try:
                cache.delete(display_cache_keys.snapshot_transition(int(school_id), int(r), str(d)))
            except Exception:
                pass
            try:
                cache.delete(display_cache_keys.snapshot_lock(int(school_id), int(r), str(d)))
            except Exception:
                pass
            try:
                cache.delete(display_cache_keys.school_snapshot_stale(int(school_id), str(d)))
            except Exception:
                pass

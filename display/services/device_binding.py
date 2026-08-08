"""
Device binding utilities for display screens.

Enforces atomic single-device binding to prevent race conditions.
Used by both HTTP snapshot endpoint and WebSocket consumer.
"""

import hashlib
import logging
from typing import Optional

from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from core.models import DisplayScreen

logger = logging.getLogger(__name__)

TOKEN_MAP_TTL = 60 * 60 * 24  # 24h


class DeviceBindingError(Exception):
    """Base exception for device binding failures."""
    pass


class ScreenBoundError(DeviceBindingError):
    """Screen is already bound to a different device."""
    pass


class ScreenNotFoundError(DeviceBindingError):
    """Screen token not found."""
    pass


def _token_map_key(token: str) -> str:
    token_hash = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    return f"display:token_map:{token_hash}"


def _cache_screen(token: str, *, screen_id: int, school_id: int, bound_device_id: str | None) -> None:
    try:
        cache.set(
            _token_map_key(token),
            {
                "id": int(screen_id),
                "school_id": int(school_id),
                "bound_device_id": (bound_device_id or "").strip() or None,
            },
            timeout=TOKEN_MAP_TTL,
        )
    except Exception:
        pass


def _screen_from_cached_map(cached_map: object, *, device_id: str) -> DisplayScreen | None:
    if not isinstance(cached_map, dict):
        return None

    try:
        bound_device_id = (cached_map.get("bound_device_id") or "").strip()
        if not bound_device_id or bound_device_id != (device_id or "").strip():
            return None

        screen_id = int(cached_map.get("id") or 0)
        school_id = int(cached_map.get("school_id") or 0)
        if screen_id <= 0 or school_id <= 0:
            return None

        screen = DisplayScreen(
            id=screen_id,
            school_id=school_id,
            bound_device_id=bound_device_id,
            is_active=True,
        )
        screen.pk = screen_id
        return screen
    except Exception:
        return None


def _mark_screen_present(screen: DisplayScreen, *, token: str) -> DisplayScreen:
    """Record presence for every successful binding lookup.

    ``bind_device_atomic`` is shared by the snapshot, status, and websocket
    paths. Keeping the touch here prevents a working display from becoming
    invisible when one caller forgets to record its heartbeat.
    """
    try:
        from core.display_presence import touch_display_presence

        touch_display_presence(int(screen.pk), token=token)
    except Exception:
        # Presence is operational telemetry and must never interrupt playback.
        pass
    return screen


def bind_device_atomic(
    token: str, 
    device_id: str, 
    allow_multi_device: Optional[bool] = None
) -> DisplayScreen:
    """
    Atomically bind a device to a display screen.
    
    Args:
        token: Screen token (DisplayScreen.token)
        device_id: Client device identifier (browser fingerprint)
        allow_multi_device: Override for DISPLAY_ALLOW_MULTI_DEVICE
        
    Returns:
        DisplayScreen instance (bound or already bound to this device)
        
    Raises:
        ScreenNotFoundError: Token not found or inactive
        ScreenBoundError: Screen already bound to different device
    
    Thread-safe: Uses conditional UPDATE (WHERE bound_device_id IS NULL)
    """
    if allow_multi_device is None:
        allow_multi_device = getattr(settings, "DISPLAY_ALLOW_MULTI_DEVICE", False)

    token = (token or "").strip()
    device_id = (device_id or "").strip()
    if not token:
        raise ScreenNotFoundError("Screen token not found or inactive")

    # Hot-path optimization: once a screen is already bound to this exact device,
    # avoid a DB read on every /status poll and snapshot fetch.
    # We intentionally do NOT trust cache-only mismatches for rejection because
    # admins may unbind a screen and we must not reject based on stale cache.
    if not allow_multi_device and device_id:
        try:
            cached_screen = _screen_from_cached_map(
                cache.get(_token_map_key(token)),
                device_id=device_id,
            )
            if cached_screen is not None:
                return _mark_screen_present(cached_screen, token=token)
        except Exception:
            pass
    
    try:
        screen = DisplayScreen.objects.select_related("school").get(
            token__iexact=token,
            is_active=True
        )
    except DisplayScreen.DoesNotExist:
        logger.warning(f"bind_device_atomic: token not found: {token[:8]}...")
        raise ScreenNotFoundError(f"Screen token not found or inactive")
    
    # If multi-device allowed, skip binding enforcement
    if allow_multi_device:
        _cache_screen(
            token,
            screen_id=int(screen.id),
            school_id=int(screen.school_id or 0),
            bound_device_id=getattr(screen, "bound_device_id", None),
        )
        logger.info(f"Multi-device enabled for screen {screen.id}, skipping binding")
        return _mark_screen_present(screen, token=token)
    
    # Check if already bound to THIS device
    if screen.bound_device_id == device_id:
        _cache_screen(
            token,
            screen_id=int(screen.id),
            school_id=int(screen.school_id or 0),
            bound_device_id=device_id,
        )
        logger.debug(f"Screen {screen.id} already bound to device {device_id[:8]}...")
        return _mark_screen_present(screen, token=token)
    
    # Check if bound to DIFFERENT device
    if screen.bound_device_id and screen.bound_device_id != device_id:
        logger.warning(
            f"Screen {screen.id} bound to {screen.bound_device_id[:8]}..., "
            f"rejecting {device_id[:8]}..."
        )
        raise ScreenBoundError(
            f"This screen is already active on another device. "
            f"Only one device can display a screen at a time."
        )
    
    # Atomic binding: UPDATE only if bound_device_id IS NULL
    rows_updated = DisplayScreen.objects.filter(
        id=screen.id,
    ).filter(
        Q(bound_device_id__isnull=True) | Q(bound_device_id="")
    ).update(
        bound_device_id=device_id,
        bound_at=timezone.now()
    )
    
    if rows_updated == 0:
        # Race condition: another device bound it between SELECT and UPDATE
        screen.refresh_from_db()
        if screen.bound_device_id == device_id:
            # We won! (unlikely but possible if multiple requests from same device)
            _cache_screen(
                token,
                screen_id=int(screen.id),
                school_id=int(screen.school_id or 0),
                bound_device_id=device_id,
            )
            logger.info(f"Screen {screen.id} bound to device {device_id[:8]}... (race won)")
            return _mark_screen_present(screen, token=token)
        else:
            # Another device won the race
            logger.warning(
                f"Screen {screen.id} bound to {screen.bound_device_id[:8]}... "
                f"during race, rejecting {device_id[:8]}..."
            )
            raise ScreenBoundError(
                f"This screen was just activated on another device. "
                f"Only one device can display a screen at a time."
            )
    
    # Success: we bound it
    screen.refresh_from_db()
    _cache_screen(
        token,
        screen_id=int(screen.id),
        school_id=int(screen.school_id or 0),
        bound_device_id=device_id,
    )
    logger.info(f"Screen {screen.id} newly bound to device {device_id[:8]}...")
    return _mark_screen_present(screen, token=token)


PREVIEW_HEADER = "X-Display-Preview"
PREVIEW_PARAM = "preview"


def _preview_requested(request) -> bool:
    try:
        raw = (request.headers.get(PREVIEW_HEADER) or "").strip().lower()
    except Exception:
        raw = ""
    if not raw:
        try:
            raw = (request.GET.get(PREVIEW_PARAM) or "").strip().lower()
        except Exception:
            raw = ""
    return raw in {"1", "true", "yes", "on"}


def resolve_preview_screen(request, token: str):
    """Return the screen when a signed-in manager is previewing it, else ``None``.

    The dashboard offers "فتح" and "فتح المعاينة" next to every screen, and both
    open the very same display URL the TV uses. Without this path the manager's
    own browser wins the race for ``bound_device_id`` and the TV is met with
    "هذه الشاشة مفعّلة على جهاز آخر" — the most common first-day failure.

    A preview therefore claims nothing: no binding, and deliberately no presence
    touch either, so the dashboard never reports a screen as live because a
    manager glanced at it from a laptop.

    The flag alone grants nothing. It is honoured only for an authenticated user
    who already has access to that screen's school, so the single-device rule
    stays intact for anyone holding just the token.
    """
    if not _preview_requested(request):
        return None

    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    token = (token or "").strip()
    if not token:
        return None

    try:
        screen = (
            DisplayScreen.objects
            .select_related("school")
            .get(token__iexact=token, is_active=True)
        )
    except DisplayScreen.DoesNotExist:
        return None
    except Exception:
        logger.exception("resolve_preview_screen lookup failed")
        return None

    if bool(getattr(user, "is_superuser", False)):
        return screen

    try:
        profile = getattr(user, "profile", None)
        if profile is None:
            return None
        if not profile.schools.filter(pk=screen.school_id).exists():
            return None
    except Exception:
        logger.exception("resolve_preview_screen membership check failed")
        return None

    return screen


def unbind_device(screen_id: int) -> bool:
    """
    Unbind device from screen (for admin/debug purposes).

    Returns:
        True if unbound, False if screen not found
    """
    screen = (
        DisplayScreen.objects
        .filter(id=screen_id)
        .only("id", "token")
        .first()
    )
    rows_updated = DisplayScreen.objects.filter(id=screen_id).update(
        bound_device_id=None,
        bound_at=None
    )
    if rows_updated:
        try:
            token = (getattr(screen, "token", "") or "").strip()
            if token:
                cache.delete(_token_map_key(token))
        except Exception:
            pass
        logger.info(f"Unbound device from screen {screen_id}")
        return True
    return False

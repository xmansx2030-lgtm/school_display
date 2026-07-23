from __future__ import annotations

import hashlib
import ipaddress
import logging
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoginRateLimitStatus:
    blocked: bool
    retry_after_seconds: int


def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", errors="ignore")).hexdigest()[:32]


def _client_ip(request) -> str:
    """Return a normalized best-effort client address without storing raw PII."""
    candidates = [
        (request.META.get("HTTP_CF_CONNECTING_IP") or "").strip(),
        (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip(),
        (request.META.get("REMOTE_ADDR") or "").strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ipaddress.ip_address(candidate).compressed
        except ValueError:
            continue
    return "unknown"


def _keys(request, identifier: str) -> tuple[str, str]:
    normalized_identifier = (identifier or "").strip().casefold()
    return (
        f"security:login:account:{_digest(normalized_identifier)}",
        f"security:login:ip:{_digest(_client_ip(request))}",
    )


def _limits() -> tuple[int, int, int]:
    window = max(60, int(getattr(settings, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900)))
    account_limit = max(1, int(getattr(settings, "LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS", 8)))
    ip_limit = max(account_limit, int(getattr(settings, "LOGIN_RATE_LIMIT_IP_ATTEMPTS", 30)))
    return window, account_limit, ip_limit


def login_rate_limit_status(request, identifier: str) -> LoginRateLimitStatus:
    window, account_limit, ip_limit = _limits()
    account_key, ip_key = _keys(request, identifier)
    try:
        account_attempts = int(cache.get(account_key) or 0)
        ip_attempts = int(cache.get(ip_key) or 0)
    except Exception:
        logger.exception("login_rate_limit cache_read_failed")
        return LoginRateLimitStatus(blocked=False, retry_after_seconds=0)

    blocked = account_attempts >= account_limit or ip_attempts >= ip_limit
    return LoginRateLimitStatus(blocked=blocked, retry_after_seconds=window if blocked else 0)


def record_failed_login(request, identifier: str) -> None:
    window, _account_limit, _ip_limit = _limits()
    for key in _keys(request, identifier):
        try:
            if cache.add(key, 1, timeout=window):
                continue
            cache.incr(key)
        except Exception:
            logger.exception("login_rate_limit cache_write_failed")


def clear_login_rate_limit(request, identifier: str) -> None:
    """Clear only the account counter after a successful login.

    The IP counter intentionally remains until the window expires so one client
    cannot cycle through many accounts after a single successful login.
    """
    account_key, _ip_key = _keys(request, identifier)
    try:
        cache.delete(account_key)
    except Exception:
        logger.exception("login_rate_limit cache_delete_failed")

"""Single-session enforcement for authenticated dashboard accounts."""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.dispatch import receiver

from core.models import UserSessionState


logger = logging.getLogger(__name__)
REPLACED_SESSION_PREFIX = "auth:replaced-session:v1:"
ACTIVE_SESSION_PREFIX = "auth:active-session:v1:"


def _session_fingerprint(session_key: str) -> str:
    return hashlib.sha256((session_key or "").encode("utf-8")).hexdigest()


def replaced_session_cache_key(session_key: str) -> str:
    return f"{REPLACED_SESSION_PREFIX}{_session_fingerprint(session_key)}"


def active_session_cache_key(user_id: int) -> str:
    return f"{ACTIVE_SESSION_PREFIX}{int(user_id)}"


def mark_session_replaced(session_key: str) -> None:
    if not session_key:
        return
    try:
        cache.set(
            replaced_session_cache_key(session_key),
            "1",
            timeout=max(300, int(getattr(settings, "SESSION_COOKIE_AGE", 3600) or 3600)),
        )
    except Exception:
        pass


def was_session_replaced(session_key: str) -> bool:
    if not session_key:
        return False
    try:
        return bool(cache.get(replaced_session_cache_key(session_key)))
    except Exception:
        return False


def cache_active_session(user_id: int, session_key: str) -> None:
    try:
        cache.set(
            active_session_cache_key(user_id),
            session_key,
            timeout=max(300, int(getattr(settings, "SESSION_COOKIE_AGE", 3600) or 3600)),
        )
    except Exception:
        pass


def active_session_for_user(user_id: int) -> str:
    try:
        cached = (cache.get(active_session_cache_key(user_id)) or "").strip()
    except Exception:
        cached = ""
    if cached:
        return cached

    try:
        active = (
            UserSessionState.objects.filter(user_id=user_id)
            .values_list("active_session_key", flat=True)
            .first()
            or ""
        ).strip()
    except Exception:
        return ""
    if active:
        cache_active_session(user_id, active)
    return active


def activate_user_session(request, user) -> str:
    """Make ``request`` the user's only valid authenticated session.

    The state row is durable, while deleting the former Django session makes
    the previous browser unauthenticated immediately—even before it navigates.
    """
    if request is None or user is None or not getattr(user, "pk", None):
        return ""

    session_key = (getattr(request.session, "session_key", None) or "").strip()
    if not session_key:
        request.session.save()
        session_key = (getattr(request.session, "session_key", None) or "").strip()
    if not session_key:
        return ""

    previous_key = ""
    try:
        with transaction.atomic():
            state = UserSessionState.objects.select_for_update().filter(user_id=user.pk).first()
            if state is None:
                UserSessionState.objects.create(
                    user_id=user.pk,
                    active_session_key=session_key,
                )
            else:
                previous_key = (state.active_session_key or "").strip()
                if previous_key != session_key:
                    state.active_session_key = session_key
                    state.save(update_fields=["active_session_key", "activated_at"])
    except IntegrityError:
        # Two first-ever logins may race before the one-to-one row exists. The
        # winner creates it; the loser then locks that row and becomes newest.
        with transaction.atomic():
            state = UserSessionState.objects.select_for_update().get(user_id=user.pk)
            previous_key = (state.active_session_key or "").strip()
            if previous_key != session_key:
                state.active_session_key = session_key
                state.save(update_fields=["active_session_key", "activated_at"])

    cache_active_session(user.pk, session_key)

    if previous_key and previous_key != session_key:
        mark_session_replaced(previous_key)
        try:
            Session.objects.filter(session_key=previous_key).delete()
        except Exception:
            logger.exception("Unable to delete replaced session for user_id=%s", user.pk)

    return session_key


@receiver(user_logged_in, dispatch_uid="core.activate_single_user_session")
def _activate_session_after_login(sender, request, user, **kwargs):
    activate_user_session(request, user)

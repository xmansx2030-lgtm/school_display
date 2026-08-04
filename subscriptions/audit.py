"""Append-only audit trail for billing actions.

Any change to what a customer paid or what access they were granted has to be
attributable. Records are written on the success path of the action and are
never updated or deleted afterwards.
"""

from __future__ import annotations

import logging

from .models import SubscriptionAuditLog


logger = logging.getLogger(__name__)


def _client_ip(request) -> str:
    if request is None:
        return ""
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    return (forwarded or request.META.get("REMOTE_ADDR") or "")[:45]


def record(
    action: str,
    *,
    school=None,
    subscription=None,
    actor=None,
    request=None,
    amount=None,
    summary: str = "",
    context: dict | None = None,
) -> SubscriptionAuditLog | None:
    """Write one audit entry. Never raises — auditing must not break the action."""
    try:
        if actor is None and request is not None:
            candidate = getattr(request, "user", None)
            if getattr(candidate, "is_authenticated", False):
                actor = candidate

        return SubscriptionAuditLog.objects.create(
            action=action,
            school=school or getattr(subscription, "school", None),
            subscription=subscription,
            actor=actor,
            actor_label=(
                (getattr(actor, "get_full_name", lambda: "")() or getattr(actor, "username", ""))
                if actor is not None
                else "system"
            )[:150],
            amount=amount,
            summary=str(summary or "")[:500],
            context=context if isinstance(context, dict) else {},
            ip_address=_client_ip(request),
        )
    except Exception:
        logger.exception("subscription_audit_write_failed action=%s", action)
        return None

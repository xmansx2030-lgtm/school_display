"""Email ownership verification.

A verified address is what makes invoices deliverable and password recovery
trustworthy, so verification is required before a school can pay — but never
before it can try the product, which would cost conversions for no benefit.

Tokens are signed rather than stored: no extra table, no cleanup job, and a
token stops working on its own once it ages out.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core import signing
from django.utils import timezone


logger = logging.getLogger(__name__)

SALT = "core.email-verification"


class EmailVerificationError(Exception):
    """Raised when a verification token is invalid, expired, or stale."""


def max_age_seconds() -> int:
    return int(getattr(settings, "EMAIL_VERIFICATION_TIMEOUT", 7 * 24 * 3600) or 7 * 24 * 3600)


def make_token(user) -> str:
    """Sign a token bound to both the account and the address being proven.

    Including the address means a token stops working if the user changes
    their email before clicking the link.
    """
    return signing.dumps(
        {"uid": int(user.pk), "email": (user.email or "").strip().casefold()},
        salt=SALT,
    )


def verify_token(token: str):
    """Return the user for a valid token, or raise ``EmailVerificationError``."""
    from django.contrib.auth import get_user_model

    try:
        payload = signing.loads(str(token or ""), salt=SALT, max_age=max_age_seconds())
    except signing.SignatureExpired as exc:
        raise EmailVerificationError("انتهت صلاحية رابط التوثيق.") from exc
    except signing.BadSignature as exc:
        raise EmailVerificationError("رابط التوثيق غير صالح.") from exc

    if not isinstance(payload, dict):
        raise EmailVerificationError("رابط التوثيق غير صالح.")

    user = get_user_model().objects.filter(pk=payload.get("uid"), is_active=True).first()
    if user is None:
        raise EmailVerificationError("الحساب غير متاح.")

    if (user.email or "").strip().casefold() != str(payload.get("email") or ""):
        raise EmailVerificationError("تم تغيير البريد الإلكتروني بعد إصدار الرابط.")

    return user


def mark_verified(user) -> bool:
    """Flag the account's email as verified. Returns True if this changed anything."""
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    if profile.email_verified_at is not None:
        return False

    profile.email_verified_at = timezone.now()
    profile.save(update_fields=["email_verified_at"])
    return True


def user_email_is_verified(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    # Platform staff are provisioned internally and are not part of this flow.
    if getattr(user, "is_superuser", False):
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.email_verified_at is not None)

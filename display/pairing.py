from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import DisplayPairingSession


DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{16,64}$")
ARABIC_DIGITS_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def pairing_ttl_seconds() -> int:
    try:
        raw = int(getattr(settings, "DISPLAY_PAIRING_TTL_SEC", 600) or 600)
    except (TypeError, ValueError):
        raw = 600
    return max(180, min(raw, 1800))


def normalize_device_id(value: str | None) -> str:
    device_id = (value or "").strip()
    return device_id if DEVICE_ID_RE.fullmatch(device_id) else ""


def normalize_user_code(value: str | None) -> str:
    translated = (value or "").translate(ARABIC_DIGITS_TRANS)
    return re.sub(r"\D+", "", translated)[:6]


def format_user_code(value: str | None) -> str:
    code = normalize_user_code(value)
    return f"{code[:3]} {code[3:]}" if len(code) == 6 else code


def _secret_hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def pairing_secret_matches(pairing: DisplayPairingSession, secret: str | None) -> bool:
    candidate = _secret_hash((secret or "").strip())
    return hmac.compare_digest(pairing.device_secret_hash, candidate)


def expire_stale_pairings() -> int:
    return DisplayPairingSession.objects.filter(
        status=DisplayPairingSession.STATUS_PENDING,
        expires_at__lte=timezone.now(),
    ).update(status=DisplayPairingSession.STATUS_EXPIRED)


def create_pairing_session(device_id: str) -> tuple[DisplayPairingSession, str]:
    normalized_device_id = normalize_device_id(device_id)
    if not normalized_device_id:
        raise ValueError("invalid_device_id")

    expire_stale_pairings()
    DisplayPairingSession.objects.filter(
        status=DisplayPairingSession.STATUS_PENDING,
        device_id=normalized_device_id,
    ).update(status=DisplayPairingSession.STATUS_CANCELLED)

    expires_at = timezone.now() + timedelta(seconds=pairing_ttl_seconds())
    for _attempt in range(30):
        user_code = f"{secrets.randbelow(1_000_000):06d}"
        device_secret = secrets.token_urlsafe(32)
        try:
            with transaction.atomic():
                pairing = DisplayPairingSession.objects.create(
                    user_code=user_code,
                    device_id=normalized_device_id,
                    device_secret_hash=_secret_hash(device_secret),
                    expires_at=expires_at,
                )
            return pairing, device_secret
        except IntegrityError:
            continue

    raise RuntimeError("pairing_code_generation_failed")


def refresh_expired_status(pairing: DisplayPairingSession) -> DisplayPairingSession:
    if (
        pairing.status == DisplayPairingSession.STATUS_PENDING
        and pairing.expires_at <= timezone.now()
    ):
        DisplayPairingSession.objects.filter(
            pk=pairing.pk,
            status=DisplayPairingSession.STATUS_PENDING,
        ).update(status=DisplayPairingSession.STATUS_EXPIRED)
        pairing.status = DisplayPairingSession.STATUS_EXPIRED
    return pairing

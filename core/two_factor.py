from __future__ import annotations

import base64
import hashlib
import hmac
import io
import logging
import secrets
import time

import pyotp
import qrcode
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from core.models import UserTwoFactorAuth


logger = logging.getLogger(__name__)
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PENDING_USER_KEY = "two_factor_pending_user_id"
PENDING_BACKEND_KEY = "two_factor_pending_backend"
PENDING_NEXT_KEY = "two_factor_pending_next"
PENDING_STARTED_KEY = "two_factor_pending_started_at"
PENDING_ATTEMPTS_KEY = "two_factor_pending_attempts"


def is_privileged_user(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if bool(getattr(user, "is_superuser", False)):
        return True
    try:
        return user.groups.filter(name="Support").exists()
    except Exception:
        return False


def two_factor_required_for(user) -> bool:
    return bool(getattr(settings, "TWO_FACTOR_REQUIRED_FOR_PRIVILEGED", True)) and is_privileged_user(user)


def _fernet() -> Fernet:
    configured = str(getattr(settings, "TWO_FACTOR_ENCRYPTION_KEY", "") or "").strip()
    if configured:
        try:
            return Fernet(configured.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise RuntimeError("TWO_FACTOR_ENCRYPTION_KEY must be a valid Fernet key") from exc

    # Safe compatibility fallback: stable while SECRET_KEY remains unchanged.
    # Production should set a dedicated key so normal SECRET_KEY rotation does
    # not invalidate enrolled authenticators.
    material = f"school-display:2fa:v1:{settings.SECRET_KEY}".encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_secret(config: UserTwoFactorAuth) -> str:
    if not config.encrypted_secret:
        raise ValueError("Two-factor secret is not provisioned")
    try:
        return _fernet().decrypt(config.encrypted_secret.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError, TypeError) as exc:
        logger.error("two_factor secret_decrypt_failed user_id=%s", config.user_id)
        raise ValueError("Two-factor secret cannot be decrypted") from exc


def get_enabled_config(user) -> UserTwoFactorAuth | None:
    try:
        config = user.two_factor_auth
    except UserTwoFactorAuth.DoesNotExist:
        return None
    return config if config.is_enabled and bool(config.encrypted_secret) else None


def ensure_setup_config(user) -> UserTwoFactorAuth:
    config, _created = UserTwoFactorAuth.objects.get_or_create(user=user)
    if not config.encrypted_secret:
        config.encrypted_secret = encrypt_secret(pyotp.random_base32())
        config.is_enabled = False
        config.recovery_code_hashes = []
        config.last_used_counter = None
        config.confirmed_at = None
        config.save()
    return config


def provisioning_uri(config: UserTwoFactorAuth) -> str:
    issuer = str(getattr(settings, "TWO_FACTOR_ISSUER", "School Display") or "School Display").strip()
    account = getattr(config.user, "email", "") or getattr(config.user, "username", str(config.user_id))
    return pyotp.TOTP(decrypt_secret(config)).provisioning_uri(name=account, issuer_name=issuer)


def qr_code_data_uri(uri: str) -> str:
    image = qrcode.make(uri)
    output = io.BytesIO()
    image.save(output, format="PNG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verify_setup_token(config: UserTwoFactorAuth, token: str) -> bool:
    normalized = "".join(ch for ch in (token or "") if ch.isdigit())
    if len(normalized) != 6:
        return False
    return bool(pyotp.TOTP(decrypt_secret(config)).verify(normalized, valid_window=1))


def _recovery_digest(code: str) -> str:
    normalized = (code or "").strip().upper().replace(" ", "")
    key = hashlib.sha256(f"2fa-recovery:{settings.SECRET_KEY}".encode("utf-8")).digest()
    return hmac.new(key, normalized.encode("ascii", errors="ignore"), hashlib.sha256).hexdigest()


def generate_recovery_codes(count: int = 10) -> list[str]:
    codes: list[str] = []
    for _ in range(max(1, count)):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(12))
        codes.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:]}")
    return codes


def enable_two_factor(config: UserTwoFactorAuth) -> list[str]:
    codes = generate_recovery_codes()
    config.is_enabled = True
    config.recovery_code_hashes = [_recovery_digest(code) for code in codes]
    config.last_used_counter = None
    config.confirmed_at = timezone.now()
    config.save(update_fields=[
        "is_enabled",
        "recovery_code_hashes",
        "last_used_counter",
        "confirmed_at",
        "updated_at",
    ])
    return codes


def _consume_totp_locked(config: UserTwoFactorAuth, token: str, *, now: float | None = None) -> bool:
    normalized = "".join(ch for ch in (token or "") if ch.isdigit())
    if len(normalized) != 6:
        return False

    totp = pyotp.TOTP(decrypt_secret(config))
    current_counter = int(now if now is not None else time.time()) // int(totp.interval)
    for counter in range(current_counter - 1, current_counter + 2):
        if counter < 0:
            continue
        expected = totp.generate_otp(counter)
        if not constant_time_compare(expected, normalized):
            continue
        if config.last_used_counter is not None and counter <= int(config.last_used_counter):
            return False
        config.last_used_counter = counter
        config.save(update_fields=["last_used_counter", "updated_at"])
        return True
    return False


def _consume_recovery_locked(config: UserTwoFactorAuth, token: str) -> bool:
    candidate = _recovery_digest(token)
    hashes = list(config.recovery_code_hashes or [])
    for index, stored in enumerate(hashes):
        if hmac.compare_digest(str(stored), candidate):
            hashes.pop(index)
            config.recovery_code_hashes = hashes
            config.save(update_fields=["recovery_code_hashes", "updated_at"])
            return True
    return False


def consume_second_factor(user, token: str, *, now: float | None = None) -> bool:
    with transaction.atomic():
        try:
            config = UserTwoFactorAuth.objects.select_for_update().get(user=user, is_enabled=True)
        except UserTwoFactorAuth.DoesNotExist:
            return False

        compact = (token or "").strip().upper().replace(" ", "")
        if compact.replace("-", "").isdigit():
            return _consume_totp_locked(config, compact, now=now)
        return _consume_recovery_locked(config, compact)


def reset_two_factor(user) -> None:
    UserTwoFactorAuth.objects.filter(user=user).delete()


def begin_challenge(request, user, *, backend: str, next_url: str) -> None:
    # Rotate the anonymous session identifier immediately after the password
    # step. Django rotates it again after the final login; doing it here also
    # prevents a fixed pre-authentication session from carrying the challenge.
    request.session.cycle_key()
    request.session[PENDING_USER_KEY] = int(user.pk)
    request.session[PENDING_BACKEND_KEY] = str(backend or "django.contrib.auth.backends.ModelBackend")
    request.session[PENDING_NEXT_KEY] = str(next_url or "/dashboard/")
    request.session[PENDING_STARTED_KEY] = int(time.time())
    request.session[PENDING_ATTEMPTS_KEY] = 0
    request.session.modified = True


def clear_challenge(request) -> None:
    for key in (
        PENDING_USER_KEY,
        PENDING_BACKEND_KEY,
        PENDING_NEXT_KEY,
        PENDING_STARTED_KEY,
        PENDING_ATTEMPTS_KEY,
    ):
        request.session.pop(key, None)


def challenge_payload(request) -> dict | None:
    try:
        user_id = int(request.session.get(PENDING_USER_KEY) or 0)
        started_at = int(request.session.get(PENDING_STARTED_KEY) or 0)
    except (TypeError, ValueError):
        return None
    ttl = int(getattr(settings, "TWO_FACTOR_CHALLENGE_TTL_SECONDS", 300) or 300)
    if user_id <= 0 or started_at <= 0 or int(time.time()) - started_at > ttl:
        clear_challenge(request)
        return None
    return {
        "user_id": user_id,
        "backend": str(request.session.get(PENDING_BACKEND_KEY) or "django.contrib.auth.backends.ModelBackend"),
        "next_url": str(request.session.get(PENDING_NEXT_KEY) or "/dashboard/"),
        "attempts": int(request.session.get(PENDING_ATTEMPTS_KEY) or 0),
    }

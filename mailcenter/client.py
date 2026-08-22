from __future__ import annotations

from typing import Any

import requests
from django.conf import settings


class ResendAPIError(RuntimeError):
    pass


def retrieve_email(provider_id: str, *, inbound: bool) -> dict[str, Any]:
    api_key = str(getattr(settings, "RESEND_API_KEY", "") or "").strip()
    if not api_key:
        raise ResendAPIError("RESEND_API_KEY is not configured")

    segment = f"emails/receiving/{provider_id}" if inbound else f"emails/{provider_id}"
    url = f"{settings.RESEND_API_BASE_URL}/{segment}"
    try:
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                "User-Agent": "school-display-mailcenter/1.0",
            },
            timeout=int(settings.RESEND_HTTP_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        raise ResendAPIError(f"Resend request failed: {exc}") from exc

    if response.status_code != 200:
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("message") or payload.get("error") or "")
        except (ValueError, AttributeError):
            detail = ""
        raise ResendAPIError(
            f"Resend returned HTTP {response.status_code}"
            + (f": {detail[:300]}" if detail else "")
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise ResendAPIError("Resend returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ResendAPIError("Resend returned an unexpected response")
    return data

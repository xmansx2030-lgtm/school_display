from __future__ import annotations

import hashlib
import json
import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST


logger = logging.getLogger(__name__)
MAX_REPORT_BYTES = 64 * 1024


def _safe_location(value: object) -> str:
    raw = str(value or "").strip()[:2048]
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme in {"http", "https"}:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:512]
    except Exception:
        pass
    return raw.split("?", 1)[0][:512]


def _client_bucket(request) -> str:
    raw = (
        request.META.get("HTTP_CF_CONNECTING_IP")
        or request.META.get("REMOTE_ADDR")
        or "unknown"
    )
    digest = hashlib.sha256(str(raw).encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"security:csp-report:{digest}"


def _report_log_limit_per_minute() -> int:
    try:
        value = int(getattr(settings, "CSP_REPORT_LOG_LIMIT_PER_MINUTE", 5) or 5)
    except (TypeError, ValueError):
        value = 5
    return max(1, min(60, value))


@csrf_exempt
@require_POST
def csp_report(request):
    """Accept browser CSP reports without persisting untrusted report bodies."""
    try:
        content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        content_length = 0
    if content_length > MAX_REPORT_BYTES:
        return HttpResponse(status=204)

    try:
        if cache.add(_client_bucket(request), 1, timeout=60):
            allowed = True
        else:
            allowed = int(cache.incr(_client_bucket(request))) <= _report_log_limit_per_minute()
    except Exception:
        allowed = True
    if not allowed:
        return HttpResponse(status=204)

    try:
        payload = json.loads(request.body[:MAX_REPORT_BYTES] or b"{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return HttpResponse(status=204)

    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return HttpResponse(status=204)
    report = payload.get("csp-report") or payload.get("body") or payload
    if not isinstance(report, dict):
        return HttpResponse(status=204)

    logger.warning(
        "csp_violation directive=%s blocked=%s document=%s disposition=%s",
        str(report.get("effective-directive") or report.get("violated-directive") or "")[:120],
        _safe_location(report.get("blocked-uri") or report.get("blockedURL")),
        _safe_location(report.get("document-uri") or report.get("documentURL")),
        str(report.get("disposition") or "report")[:32],
    )
    return HttpResponse(status=204)

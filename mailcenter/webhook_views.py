from __future__ import annotations

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from svix.webhooks import Webhook, WebhookVerificationError

from .services import process_resend_event


logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def resend_webhook(request):
    secret = str(getattr(settings, "RESEND_WEBHOOK_SECRET", "") or "").strip()
    if not secret:
        return JsonResponse({"detail": "webhook_not_configured"}, status=503)

    event_id = (request.headers.get("svix-id") or "").strip()
    headers = {
        "svix-id": event_id,
        "svix-timestamp": (request.headers.get("svix-timestamp") or "").strip(),
        "svix-signature": (request.headers.get("svix-signature") or "").strip(),
    }
    if not all(headers.values()):
        return JsonResponse({"detail": "missing_signature"}, status=403)

    try:
        Webhook(secret).verify(request.body, headers)
    except (WebhookVerificationError, ValueError, TypeError):
        logger.warning("resend_webhook_signature_rejected event_id=%s", event_id[:80])
        return JsonResponse({"detail": "invalid_signature"}, status=403)

    try:
        verified = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"detail": "invalid_json"}, status=400)
    if not isinstance(verified, dict):
        return JsonResponse({"detail": "invalid_payload"}, status=400)

    try:
        message, created = process_resend_event(verified, event_id=event_id)
    except ValueError:
        return JsonResponse({"detail": "unsupported_event"}, status=400)
    except Exception:
        logger.exception("resend_webhook_processing_failed event_id=%s", event_id[:80])
        return JsonResponse({"detail": "processing_failed"}, status=500)
    return JsonResponse({"ok": True, "created": created, "message_id": message.pk})

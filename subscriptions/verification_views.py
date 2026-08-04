"""Customer-facing email verification actions."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from core.email_verification import user_email_is_verified
from core.tenant_access import authorized_active_school

from .email_notifications import requeue_email_verification
from .models import SchoolSubscription


logger = logging.getLogger(__name__)

RESEND_COOLDOWN_SECONDS = 120


@login_required(login_url="dashboard:login")
@require_POST
def resend_email_verification(request):
    """Send the verification link again, rate limited per account."""
    if user_email_is_verified(request.user):
        messages.info(request, "بريدك الإلكتروني مؤكد بالفعل.")
        return redirect("dashboard:my_subscription")

    if not (request.user.email or "").strip():
        messages.error(request, "لا يوجد بريد إلكتروني مسجل على حسابك. حدّثه أولاً من الإعدادات.")
        return redirect("dashboard:my_subscription")

    cooldown_key = f"verify-email:resend:{request.user.pk}"
    try:
        fresh = cache.add(cooldown_key, 1, timeout=RESEND_COOLDOWN_SECONDS)
    except Exception:
        fresh = True
    if not fresh:
        messages.info(request, "أرسلنا الرابط قبل قليل. تحقق من بريدك أو انتظر دقيقتين قبل إعادة الإرسال.")
        return redirect("dashboard:my_subscription")

    profile = getattr(request.user, "profile", None)
    school = authorized_active_school(profile, user=request.user) if profile else None
    subscription = (
        SchoolSubscription.objects.filter(school=school).order_by("-created_at").first()
        if school is not None
        else None
    )
    if subscription is None:
        messages.error(request, "تعذر إرسال رابط التأكيد لعدم وجود اشتراك مرتبط بالمدرسة.")
        return redirect("dashboard:my_subscription")

    try:
        requeue_email_verification(subscription, request.user)
    except Exception:
        logger.exception("verification_resend_failed user_id=%s", request.user.pk)
        messages.error(request, "تعذر إرسال رابط التأكيد الآن. حاول مرة أخرى بعد قليل.")
        return redirect("dashboard:my_subscription")

    messages.success(request, f"أرسلنا رابط التأكيد إلى {request.user.email}.")
    return redirect("dashboard:my_subscription")

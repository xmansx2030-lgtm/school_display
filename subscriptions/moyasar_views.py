from __future__ import annotations

import hmac
import json
import logging
import re
from datetime import date, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.models import SubscriptionPlan
from core.tenant_access import authorized_active_school

from .models import MoyasarCheckout, SchoolSubscription
from .moyasar import MoyasarAPIError, MoyasarClient, MoyasarConfigurationError
from .moyasar_processing import (
    MoyasarVerificationError,
    amount_to_minor_units,
    apply_payment_details,
)


logger = logging.getLogger(__name__)
_PAYMENT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def _active_school(request):
    try:
        profile = request.user.profile
    except Exception:
        return None
    return authorized_active_school(profile, user=request.user)


def _configured() -> bool:
    return bool(
        getattr(settings, "MOYASAR_ENABLED", False)
        and getattr(settings, "MOYASAR_PUBLISHABLE_KEY", "")
        and getattr(settings, "MOYASAR_SECRET_KEY", "")
    )


def _user_may_checkout(user) -> bool:
    if not _configured():
        return False
    return bool(
        getattr(settings, "MOYASAR_LIVE_MODE", False)
        or getattr(settings, "DEBUG", False)
        or getattr(user, "is_superuser", False)
    )


def _renewal_start(school, plan) -> date:
    today = timezone.localdate()
    current = (
        SchoolSubscription.objects.filter(
            school=school,
            plan=plan,
            status="active",
            starts_at__lte=today,
        )
        .order_by("-ends_at", "-starts_at", "-id")
        .first()
    )
    if current and current.ends_at and current.ends_at >= today:
        return current.ends_at + timedelta(days=1)
    return today


@login_required(login_url="dashboard:login")
@require_POST
def moyasar_start(request):
    if not _user_may_checkout(request.user):
        messages.error(request, "الدفع عبر ميسر بانتظار تفعيل الحساب حاليًا.")
        return redirect("dashboard:my_subscription")

    school = _active_school(request)
    if school is None:
        messages.error(request, "اختر مدرسة صالحة قبل بدء الدفع.")
        return redirect("dashboard:my_subscription")

    request_type = (request.POST.get("request_type") or "").strip()
    if request_type not in {"new", "renewal"}:
        messages.error(request, "نوع الاشتراك غير صالح.")
        return redirect("dashboard:my_subscription")

    plan = get_object_or_404(
        SubscriptionPlan.objects.filter(is_active=True),
        pk=request.POST.get("plan_id"),
    )
    amount = getattr(plan, "price", 0) or 0
    if amount <= 0:
        messages.error(request, "هذه الخطة مجانية ولا تحتاج إلى بوابة دفع.")
        return redirect("dashboard:my_subscription")

    if request_type == "renewal":
        if not SchoolSubscription.objects.filter(school=school, plan=plan).exists():
            messages.error(request, "لا يمكن تجديد خطة غير مرتبطة بمدرستك.")
            return redirect("dashboard:my_subscription")
        starts_at = _renewal_start(school, plan)
    else:
        starts_at = timezone.localdate()

    live_mode = bool(getattr(settings, "MOYASAR_LIVE_MODE", False))
    recent = (
        MoyasarCheckout.objects.filter(
            school=school,
            created_by=request.user,
            plan=plan,
            request_type=request_type,
            status="initiated",
            live_mode=live_mode,
            created_at__gte=timezone.now() - timedelta(minutes=20),
        )
        .order_by("-created_at")
        .first()
    )
    checkout = recent or MoyasarCheckout.objects.create(
        school=school,
        created_by=request.user,
        plan=plan,
        request_type=request_type,
        starts_at=starts_at,
        amount=amount,
        currency="SAR",
        live_mode=live_mode,
    )
    return redirect("subscriptions:moyasar_checkout", reference=checkout.merchant_reference)


@login_required(login_url="dashboard:login")
@require_GET
def moyasar_checkout(request, reference: str):
    if not _user_may_checkout(request.user):
        messages.error(request, "الدفع عبر ميسر بانتظار تفعيل الحساب حاليًا.")
        return redirect("dashboard:my_subscription")
    school = _active_school(request)
    checkout = get_object_or_404(
        MoyasarCheckout.objects.select_related("school", "plan"),
        merchant_reference=reference,
        school=school,
        created_by=request.user,
    )
    if checkout.status != "initiated":
        messages.info(request, "هذه المحاولة عولجت سابقًا. يمكنك مراجعة حالتها في سجل المدفوعات.")
        return redirect("dashboard:my_subscription")

    callback_base = str(getattr(settings, "MOYASAR_CALLBACK_BASE_URL", "") or "").rstrip("/")
    callback_url = (
        f"{callback_base}{reverse('subscriptions:moyasar_return')}?"
        f"{urlencode({'reference': checkout.merchant_reference})}"
    )
    config = {
        "amount": amount_to_minor_units(checkout.amount),
        "currency": checkout.currency,
        "description": f"School Display - {checkout.plan.name} - {checkout.merchant_reference}",
        "publishable_api_key": str(settings.MOYASAR_PUBLISHABLE_KEY),
        "callback_url": callback_url,
        "supported_networks": ["mada", "visa", "mastercard"],
        "methods": ["creditcard"],
        "fixed_width": False,
        "metadata": {
            "merchant_reference": checkout.merchant_reference,
            "school_id": str(checkout.school_id),
            "plan_id": str(checkout.plan_id),
        },
    }
    return render(
        request,
        "subscriptions/moyasar_checkout.html",
        {"checkout": checkout, "moyasar_config": config},
    )


@login_required(login_url="dashboard:login")
@require_GET
def moyasar_return(request):
    school = _active_school(request)
    reference = (request.GET.get("reference") or "").strip()
    payment_id = (request.GET.get("id") or "").strip()
    checkout = MoyasarCheckout.objects.filter(
        merchant_reference=reference,
        school=school,
        created_by=request.user,
    ).first()
    if checkout is None or not _PAYMENT_ID_RE.fullmatch(payment_id):
        messages.error(request, "تعذر العثور على عملية ميسر المرتبطة بالحساب.")
        return redirect("dashboard:my_subscription")

    try:
        details = MoyasarClient().fetch_payment(payment_id)
        checkout = apply_payment_details(checkout.pk, details, event_type="return")
    except (MoyasarConfigurationError, MoyasarAPIError, MoyasarVerificationError):
        logger.warning("moyasar_return_verification_failed reference=%s", reference)
        messages.error(request, "تعذر تأكيد دفعة ميسر. لم يتم تفعيل الاشتراك.")
        return redirect("dashboard:my_subscription")

    if checkout.status in {"paid", "captured"}:
        if checkout.payment_operation_id:
            messages.success(request, "اكتمل الدفع عبر ميسر وتم تفعيل الاشتراك.")
        else:
            messages.success(request, "نجحت دفعة الاختبار في ميسر، ولم يُفعّل اشتراك حقيقي.")
    elif checkout.status == "initiated":
        messages.info(request, "عملية ميسر ما زالت قيد التحقق. سنحدّث حالتها تلقائيًا.")
    else:
        messages.error(request, "لم تكتمل عملية ميسر، ولم يتم تفعيل الاشتراك.")
    return redirect("dashboard:my_subscription")


@csrf_exempt
@require_POST
def moyasar_webhook(request):
    if not getattr(settings, "MOYASAR_ENABLED", False):
        return JsonResponse({"detail": "disabled"}, status=503)
    expected_secret = str(getattr(settings, "MOYASAR_WEBHOOK_SECRET", "") or "")
    if not expected_secret:
        return JsonResponse({"detail": "webhook_secret_missing"}, status=503)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"detail": "invalid_json"}, status=400)
    if not isinstance(payload, dict) or not hmac.compare_digest(
        str(payload.get("secret_token") or ""),
        expected_secret,
    ):
        return JsonResponse({"detail": "forbidden"}, status=403)

    details = payload.get("data")
    if not isinstance(details, dict):
        return JsonResponse({"detail": "missing_payment"}, status=400)
    metadata = details.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    reference = str(metadata.get("merchant_reference") or "").strip()
    checkout = MoyasarCheckout.objects.filter(merchant_reference=reference).first()
    if checkout is None:
        return JsonResponse({"detail": "checkout_not_found"}, status=404)
    try:
        apply_payment_details(
            checkout.pk,
            details,
            event_type=str(payload.get("type") or "webhook"),
        )
    except MoyasarVerificationError:
        logger.warning("moyasar_webhook_verification_failed reference=%s", reference)
        return JsonResponse({"detail": "verification_failed"}, status=409)
    return JsonResponse({"ok": True})

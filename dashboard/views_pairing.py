from __future__ import annotations

import hashlib

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import DisplayPairingSession, DisplayScreen
from display.pairing import format_user_code, normalize_user_code, refresh_expired_status

from .access import get_active_school_or_redirect
from .decorators import manager_required


def _lookup_rate_limited(request) -> bool:
    limit = int(getattr(settings, "DISPLAY_PAIRING_CODE_ATTEMPTS", 8) or 8)
    user_id = int(getattr(request.user, "pk", 0) or 0)
    remote_addr = (request.META.get("REMOTE_ADDR") or "unknown").strip()
    key_hash = hashlib.sha256(f"{user_id}:{remote_addr}".encode("utf-8")).hexdigest()[:24]
    key = f"display:pairing:lookup:{key_hash}"
    try:
        if cache.add(key, 1, timeout=10 * 60):
            return False
        return int(cache.incr(key)) > limit
    except Exception:
        return False


def _pairing_context(*, pairing=None, screens=None, error="", success=False):
    return {
        "pairing": pairing,
        "pairing_code": format_user_code(getattr(pairing, "user_code", "")),
        "screens": screens or [],
        "has_bound_screens": any(bool(screen.bound_device_id) for screen in (screens or [])),
        "pairing_error": error,
        "pairing_success": success,
    }


@manager_required
def screen_pairing(request):
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    error = ""
    entered_code = ""
    if request.method == "POST":
        entered_code = normalize_user_code(request.POST.get("user_code"))
        if _lookup_rate_limited(request):
            error = "تم إيقاف المحاولات مؤقتًا. تحقق من الرمز وحاول بعد دقائق."
        elif len(entered_code) != 6:
            error = "أدخل الرمز المكوّن من 6 أرقام كما يظهر على التلفاز."
        else:
            pairing = (
                DisplayPairingSession.objects.filter(
                    user_code=entered_code,
                    status=DisplayPairingSession.STATUS_PENDING,
                    expires_at__gt=timezone.now(),
                )
                .order_by("-created_at")
                .first()
            )
            if pairing is not None:
                return redirect("dashboard:screen_pairing_confirm", pairing_id=pairing.pk)
            error = "الرمز غير صحيح أو انتهت صلاحيته. أنشئ رمزًا جديدًا من التلفاز."

    return render(
        request,
        "dashboard/tv_pairing.html",
        {
            **_pairing_context(error=error),
            "entered_code": entered_code,
            "school": school,
        },
    )


@manager_required
def screen_pairing_confirm(request, pairing_id):
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    pairing = get_object_or_404(
        DisplayPairingSession.objects.select_related("screen"),
        pk=pairing_id,
    )
    refresh_expired_status(pairing)

    if pairing.status == DisplayPairingSession.STATUS_APPROVED:
        if pairing.screen_id and pairing.screen.school_id != school.pk:
            raise Http404("Pairing session does not belong to this school.")
        return render(
            request,
            "dashboard/tv_pairing.html",
            {
                **_pairing_context(pairing=pairing, success=True),
                "school": school,
            },
        )

    if pairing.status != DisplayPairingSession.STATUS_PENDING:
        message = (
            "انتهت صلاحية رمز الربط. اعرض رمزًا جديدًا على التلفاز ثم حاول مرة أخرى."
            if pairing.status == DisplayPairingSession.STATUS_EXPIRED
            else "جلسة الربط هذه لم تعد متاحة. أنشئ رمزًا جديدًا من التلفاز."
        )
        return render(
            request,
            "dashboard/tv_pairing.html",
            {
                **_pairing_context(pairing=pairing, error=message),
                "school": school,
            },
        )

    screens = list(
        DisplayScreen.objects.filter(school=school, is_active=True).order_by("name", "id")
    )
    error = ""
    selected_screen_id = request.POST.get("screen_id") if request.method == "POST" else ""

    if request.method == "POST":
        try:
            selected_pk = int(selected_screen_id or 0)
        except (TypeError, ValueError):
            selected_pk = 0

        if not selected_pk:
            error = "اختر الشاشة التي تريد عرضها على هذا التلفاز."
        else:
            replace_existing = request.POST.get("replace_existing") == "1"
            with transaction.atomic():
                locked_pairing = get_object_or_404(
                    DisplayPairingSession.objects.select_for_update(),
                    pk=pairing.pk,
                )
                if (
                    locked_pairing.status == DisplayPairingSession.STATUS_PENDING
                    and locked_pairing.expires_at <= timezone.now()
                ):
                    locked_pairing.status = DisplayPairingSession.STATUS_EXPIRED
                    locked_pairing.save(update_fields=["status", "updated_at"])
                    error = "انتهت صلاحية الرمز أثناء الربط. أنشئ رمزًا جديدًا من التلفاز."
                elif locked_pairing.status != DisplayPairingSession.STATUS_PENDING:
                    error = "تم استخدام رمز الربط أو إلغاؤه مسبقًا."
                else:
                    screen = get_object_or_404(
                        DisplayScreen.objects.select_for_update(),
                        pk=selected_pk,
                        school=school,
                        is_active=True,
                    )
                    replacing_other_device = bool(
                        screen.bound_device_id
                        and screen.bound_device_id != locked_pairing.device_id
                    )
                    if replacing_other_device and not replace_existing:
                        error = "هذه الشاشة مفعّلة حاليًا على جهاز آخر. فعّل خيار الاستبدال لتأكيد نقل العرض إلى هذا التلفاز."
                    else:
                        screen.bound_device_id = locked_pairing.device_id
                        screen.bound_at = timezone.now()
                        screen.save(update_fields=["bound_device_id", "bound_at"])

                        locked_pairing.screen = screen
                        locked_pairing.approved_by = request.user
                        locked_pairing.approved_at = timezone.now()
                        locked_pairing.status = DisplayPairingSession.STATUS_APPROVED
                        locked_pairing.save(
                            update_fields=[
                                "screen",
                                "approved_by",
                                "approved_at",
                                "status",
                                "updated_at",
                            ]
                        )

                        token_hash = hashlib.sha256((screen.token or "").encode("utf-8")).hexdigest()
                        cache.delete(f"display:token_map:{token_hash}")

                        if replacing_other_device:
                            from .views_screens import _broadcast_reload_screen_ws

                            transaction.on_commit(
                                lambda: _broadcast_reload_screen_ws(
                                    school_id=int(school.pk),
                                    screen_id=int(screen.pk),
                                )
                            )

            if not error:
                messages.success(request, "تم ربط التلفاز بنجاح وسيبدأ العرض تلقائيًا.")
                return redirect("dashboard:screen_pairing_confirm", pairing_id=pairing.pk)

    return render(
        request,
        "dashboard/tv_pairing.html",
        {
            **_pairing_context(pairing=pairing, screens=screens, error=error),
            "selected_screen_id": str(selected_screen_id or ""),
            "school": school,
        },
    )

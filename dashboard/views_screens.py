from __future__ import annotations

import hashlib
import logging
from urllib.parse import quote

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from display.ws_groups import school_group_name
from core.display_presence import display_live_threshold_seconds, latest_display_presence
from .access import get_active_school_or_redirect
from .decorators import manager_required
from .forms import DisplayScreenForm

logger = logging.getLogger(__name__)


def _display_screen_model():
    return apps.get_model("core", "DisplayScreen")


def _model_has_field(model, field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _screen_command_ttl_seconds() -> int:
    """
    Manual screen commands must outlive the client's fallback status checks.

    The display can stay in ws-live mode and only hit `/status` every 5 minutes,
    and outside the active window that heartbeat can stretch to 30 minutes.
    If the token-scoped cache key expires earlier, a missed WebSocket broadcast
    makes the command effectively disappear before the screen can observe it.
    """
    default_ttl = 65 * 60  # 65 minutes: safely above the 30-minute off-hours heartbeat.
    try:
        raw = int(getattr(settings, "DISPLAY_REMOTE_COMMAND_TTL_SEC", default_ttl) or default_ttl)
    except Exception:
        raw = default_ttl
    return max(30 * 60, min(raw, 24 * 60 * 60))


def _broadcast_reload_screen_ws(*, school_id: int, screen_id: int) -> None:
    try:
        if not getattr(settings, "DISPLAY_WS_ENABLED", False):
            return
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        async_to_sync(channel_layer.group_send)(
            school_group_name(int(school_id)),
            {
                "type": "broadcast_reload",
                "school_id": int(school_id),
                "target_screen_id": int(screen_id),
            },
        )
    except Exception:
        logger.exception(
            "screen_reload_broadcast_failed school_id=%s screen_id=%s",
            school_id,
            screen_id,
        )


def _queue_screen_reload(screen, *, school_id: int) -> bool:
    """Invalidate the rendered page and ask only this TV to reload."""
    token_value = (
        (getattr(screen, "token", None) or getattr(screen, "api_token", None) or "").strip()
    )
    if not token_value:
        return False

    token_hash = hashlib.sha256(token_value.encode("utf-8")).hexdigest()
    try:
        cache.set(
            f"display:force_reload:{token_hash}",
            "1",
            timeout=_screen_command_ttl_seconds(),
        )
        cache.delete(f"display_ctx:{token_value}")
        try:
            revision = int(screen.school.schedule_settings.schedule_revision or 0)
            cache.delete(f"display_ctx:{token_value}:rev:{revision}")
        except Exception:
            pass
    except Exception:
        pass

    try:
        transaction.on_commit(
            lambda: _broadcast_reload_screen_ws(
                school_id=int(school_id),
                screen_id=int(screen.pk),
            )
        )
    except Exception:
        _broadcast_reload_screen_ws(school_id=int(school_id), screen_id=int(screen.pk))
    return True


def _get_subscription_model_robust():
    """
    Source of truth: subscriptions.SchoolSubscription.
    Fallback to core.SchoolSubscription exists for legacy deployments only.
    """
    try:
        return apps.get_model("subscriptions", "SchoolSubscription")
    except Exception:
        logger.warning(
            "subscriptions.SchoolSubscription not available; falling back to legacy core.SchoolSubscription"
        )
        try:
            return apps.get_model("core", "SchoolSubscription")
        except Exception:
            logger.error(
                "No subscription model available (subscriptions/core). Screen limits may be degraded."
            )
            return None


def get_school_active_subscriptions_qs(school):
    """يرجع QuerySet للاشتراكات السارية (قد تكون أكثر من اشتراك)."""
    sub_model = _get_subscription_model_robust()
    if sub_model is None:
        return None

    today = timezone.localdate()
    qs = sub_model.objects.all()

    # school filter
    try:
        sub_model._meta.get_field("school")
        qs = qs.filter(school=school)
    except Exception:
        try:
            sub_model._meta.get_field("school_id")
            qs = qs.filter(school_id=getattr(school, "id", school))
        except Exception:
            return None

    # active status
    try:
        sub_model._meta.get_field("status")
        qs = qs.filter(status="active")
    except Exception:
        try:
            sub_model._meta.get_field("is_active")
            qs = qs.filter(is_active=True)
        except Exception:
            pass

    # start date
    for f in ("starts_at", "start_date"):
        try:
            sub_model._meta.get_field(f)
            qs = qs.filter(**{f"{f}__lte": today})
            break
        except Exception:
            continue

    # end date
    for f in ("ends_at", "end_date"):
        try:
            sub_model._meta.get_field(f)
            qs = qs.filter(Q(**{f"{f}__isnull": True}) | Q(**{f"{f}__gte": today}))
            break
        except Exception:
            continue

    # select plan if possible
    try:
        sub_model._meta.get_field("plan")
        qs = qs.select_related("plan").defer("plan__duration_days")
    except Exception:
        pass

    return qs


def get_school_active_subscription(school):
    """يرجع اشتراك المدرسة الساري (إن وجد) بشكل مرن بين subscriptions و legacy."""
    qs = get_school_active_subscriptions_qs(school)
    if qs is None:
        return None
    subs = list(qs)
    if not subs:
        return None

    def _key(sub):
        plan = getattr(sub, "plan", None)
        ms = getattr(plan, "max_screens", None) if plan else None
        return (1 if ms is None else 0, int(ms or 0))

    subs.sort(key=_key, reverse=True)
    return subs[0]


def get_school_max_screens_limit(school) -> int | None:
    """يرجع الحد الأقصى للشاشات حسب خطة الاشتراك. None = غير محدود."""
    try:
        from subscriptions.utils import school_effective_max_screens

        return school_effective_max_screens(getattr(school, "id", None))
    except Exception:
        pass

    qs = get_school_active_subscriptions_qs(school)
    if qs is None:
        return 0
    subs = list(qs)
    if not subs:
        return 0

    for sub in subs:
        plan = getattr(sub, "plan", None)
        if plan is not None and getattr(plan, "max_screens", None) is None:
            return None

    best = 0
    for sub in subs:
        plan = getattr(sub, "plan", None)
        ms = getattr(plan, "max_screens", None) if plan else None
        try:
            ms_i = int(ms or 0)
        except Exception:
            ms_i = 0
        if ms_i > best:
            best = ms_i
    return best


def get_school_effective_plan_label(school) -> str | None:
    """اسم الخطة المستخدمة لعرض المعلومات في الواجهة (أفضل اشتراك)."""
    sub = get_school_active_subscription(school)
    if not sub:
        return None
    plan = getattr(sub, "plan", None)
    return getattr(plan, "name", None) if plan else None


@manager_required
def screen_list(request):
    display_screen = _display_screen_model()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    try:
        from core.screen_limits import enforce_school_screen_limit

        enforce_school_screen_limit(int(getattr(school, "id", 0) or 0))
    except Exception:
        pass

    auto_disabled_count = 0
    try:
        if _model_has_field(display_screen, "auto_disabled_by_limit"):
            auto_disabled_count = display_screen.objects.filter(
                school=school,
                auto_disabled_by_limit=True,
            ).count()
    except Exception:
        auto_disabled_count = 0

    qs = display_screen.objects.filter(school=school).order_by("-created_at", "-id")
    current_count = qs.count()
    max_screens = get_school_max_screens_limit(school)
    plan_name = get_school_effective_plan_label(school)

    now = timezone.now()
    live_threshold_seconds = display_live_threshold_seconds()
    live_threshold_minutes = max(1, int(round(live_threshold_seconds / 60)))
    live_count = 0
    linked_count = 0
    screen_rows = []
    for screen in qs:
        last_seen = latest_display_presence(screen)
        bound_at = getattr(screen, "bound_at", None)
        last_activity = last_seen or bound_at
        bound_device = bool((getattr(screen, "bound_device_id", "") or "").strip())
        is_enabled = bool(getattr(screen, "is_active", False))
        is_live = False
        last_seen_seconds = None
        last_seen_display = "لم تتصل بعد"
        last_seen_full = ""
        if last_activity:
            try:
                delta = now - last_activity
                last_seen_seconds = max(0, int(delta.total_seconds()))
                is_live = bool(
                    last_seen
                    and is_enabled
                    and bound_device
                    and last_seen_seconds <= live_threshold_seconds
                )
                local_seen = timezone.localtime(last_activity)
                last_seen_full = local_seen.strftime("%Y-%m-%d %H:%M")
                if last_seen_seconds < 60:
                    last_seen_display = "قبل أقل من دقيقة"
                elif last_seen_seconds < 3600:
                    last_seen_display = f"قبل {max(1, last_seen_seconds // 60)} دقيقة"
                elif last_seen_seconds < 86400:
                    last_seen_display = f"قبل {max(1, last_seen_seconds // 3600)} ساعة"
                else:
                    last_seen_display = local_seen.strftime("%Y-%m-%d %H:%M")
            except Exception:
                is_live = False

        if bound_device:
            linked_count += 1
        if is_live:
            live_count += 1

        if not is_enabled:
            status_key = "disabled"
            status_label = "متوقفة"
            status_hint = "الشاشة غير مفعلة من لوحة التحكم"
        elif is_live:
            status_key = "live"
            status_label = "متصلة الآن"
            status_hint = "الشاشة تعمل وتستقبل التحديثات"
        elif bound_device and last_seen:
            status_key = "linked"
            status_label = "غير متصلة الآن"
            status_hint = "الجهاز مرتبط، لكن لم يصل نبض حديث"
        elif bound_device:
            status_key = "linked"
            status_label = "مرتبطة وجاهزة"
            status_hint = "تم ربط الجهاز؛ سيظهر الاتصال عند أول نبض"
        else:
            status_key = "waiting"
            status_label = "بانتظار الربط"
            status_hint = "افتح الرابط على التلفاز لربط الشاشة"

        screen.dashboard_status_key = status_key
        screen.dashboard_status_label = status_label
        screen.dashboard_status_hint = status_hint
        screen.dashboard_last_seen_display = last_seen_display
        screen.dashboard_last_seen_full = last_seen_full
        screen.dashboard_is_live = is_live
        screen.dashboard_bound_device = bound_device
        screen.dashboard_has_customization = bool(
            (getattr(screen, "theme_override", "") or "").strip()
            or (getattr(screen, "occasion_theme", "auto") or "auto") != "auto"
            or (getattr(screen, "featured_panel_override", "") or "").strip()
            or not bool(getattr(screen, "show_announcements", True))
            or not bool(getattr(screen, "show_period_classes", True))
            or not bool(getattr(screen, "show_standby", True))
            or not bool(getattr(screen, "show_duty", True))
            or not bool(getattr(screen, "show_excellence", True))
        )
        screen_rows.append(screen)

    if max_screens is None:
        screens_remaining = None
    else:
        try:
            screens_remaining = max(int(max_screens) - int(current_count), 0)
        except Exception:
            screens_remaining = 0

    if max_screens is None:
        can_create_screen = True
        show_screen_limit_message = False
        screen_limit_message = None
    else:
        can_create_screen = current_count < int(max_screens)
        show_screen_limit_message = not can_create_screen
        if max_screens <= 0:
            screen_limit_message = "لا يمكن إضافة شاشات لهذه المدرسة (لا يوجد اشتراك نشط)."
        else:
            screen_limit_message = f"لا يمكن إضافة أكثر من {int(max_screens)} شاشة لهذه المدرسة"

    return render(
        request,
        "dashboard/screen_list.html",
        {
            "screens": screen_rows,
            "can_create_screen": can_create_screen,
            "show_screen_limit_message": show_screen_limit_message,
            "screen_limit": None if max_screens is None else int(max_screens),
            "screen_limit_message": screen_limit_message,
            "screens_count": current_count,
            "screens_live_count": live_count,
            "screens_linked_count": linked_count,
            "screen_live_threshold_seconds": live_threshold_seconds,
            "screen_live_threshold_minutes": live_threshold_minutes,
            "plan_name": plan_name,
            "screens_remaining": screens_remaining,
            "auto_disabled_count": auto_disabled_count,
        },
    )


@manager_required
def screen_create(request):
    display_screen = _display_screen_model()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    current_count = display_screen.objects.filter(school=school).count()
    max_screens = get_school_max_screens_limit(school)
    if (max_screens is not None) and (current_count >= int(max_screens)):
        if max_screens <= 0:
            messages.warning(request, "لا يمكن إنشاء شاشة بدون اشتراك نشط.")
        else:
            messages.warning(request, f"لا يمكن إنشاء أكثر من {int(max_screens)} شاشة لهذه المدرسة.")
        return redirect("dashboard:screen_list")

    if request.method == "POST":
        form = DisplayScreenForm(request.POST)
        if form.is_valid():
            screen = form.save(commit=False)
            screen.school = school
            screen.save()
            messages.success(
                request,
                "تم إضافة شاشة جديدة.\n\n"
                "تنبيه مهم:\n"
                "- سيتم حفظ الشاشة على أول تلفاز/متصفح يتم فتح الرابط عليه، ولا يمكن فتحها على جهاز آخر إلا بعد فصل الجهاز من لوحة التحكم.\n"
                "- يمكنك تعديل الثيم والمحتوى الظاهر لهذه الشاشة بشكل مستقل من زر تخصيص.",
            )
            return redirect("dashboard:screen_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = DisplayScreenForm()

    return render(request, "dashboard/screen_form.html", {"form": form, "title": "إضافة شاشة"})


@manager_required
def screen_edit(request, pk: int):
    display_screen = _display_screen_model()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    screen = get_object_or_404(display_screen, pk=pk, school=school)
    if request.method == "POST":
        form = DisplayScreenForm(request.POST, instance=screen)
        if form.is_valid():
            screen = form.save()
            _queue_screen_reload(screen, school_id=int(school.pk))
            messages.success(
                request,
                f"تم حفظ تخصيص الشاشة ({screen.name}) وإرسال أمر تحديث لها.",
            )
            return redirect("dashboard:screen_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = DisplayScreenForm(instance=screen)

    return render(
        request,
        "dashboard/screen_form.html",
        {
            "form": form,
            "screen": screen,
            "title": f"تخصيص شاشة: {screen.name}",
            "is_edit": True,
        },
    )


@manager_required
@require_POST
def screen_refresh_now(request, pk: int):
    """Force a single screen to fetch new data."""
    display_screen = _display_screen_model()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    from schedule.cache_utils import get_schedule_revision_for_school_id

    obj = get_object_or_404(display_screen, pk=pk, school=school)

    token_value = ((getattr(obj, "token", None) or getattr(obj, "api_token", None) or "").strip())
    if not token_value:
        messages.error(request, "تعذر تحديث الشاشة: لا يوجد token صالح.")
        return redirect("dashboard:screen_list")

    token_hash = hashlib.sha256(token_value.encode("utf-8")).hexdigest()
    try:
        cache.set(
            f"display:force_refresh:{token_hash}",
            "1",
            timeout=_screen_command_ttl_seconds(),
        )
    except Exception:
        pass

    school_id = int(getattr(school, "id", 0) or 0)
    cur_rev = int(get_schedule_revision_for_school_id(school_id) or 0)

    def _broadcast_invalidate_screen_ws(*, screen_id: int, revision: int) -> None:
        try:
            from django.conf import settings

            if not getattr(settings, "DISPLAY_WS_ENABLED", False):
                return
        except Exception:
            return

        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            if not channel_layer:
                return

            # Every connected display must join its school group. Token-group
            # membership is best-effort and can expire independently on very
            # long-running TV connections, so target the required group and let
            # the consumer enforce the screen id.
            group = school_group_name(school_id)
            async_to_sync(channel_layer.group_send)(
                group,
                {
                    "type": "broadcast_invalidate",
                    "school_id": int(school_id),
                    "target_screen_id": int(screen_id),
                    "revision": int(revision or 0),
                    "reason": "manual_refresh",
                },
            )
        except Exception:
            return

    try:
        transaction.on_commit(
            lambda: _broadcast_invalidate_screen_ws(screen_id=int(obj.pk), revision=cur_rev)
        )
    except Exception:
        try:
            _broadcast_invalidate_screen_ws(screen_id=int(obj.pk), revision=cur_rev)
        except Exception:
            pass

    logger.info(
        "screen_refresh_now school_id=%s screen_id=%s rev=%s",
        int(school_id),
        int(getattr(obj, "id", 0) or 0),
        int(cur_rev),
    )

    messages.success(request, f"تم إرسال أمر تحديث لهذه الشاشة ({obj.name}).")

    next_url = (request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect("dashboard:screen_list")


@manager_required
@require_POST
def screen_reload_now(request, pk: int):
    """Force a single screen to reload the page (equivalent to pressing F5)."""
    display_screen = _display_screen_model()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    obj = get_object_or_404(display_screen, pk=pk, school=school)

    token_value = ((getattr(obj, "token", None) or getattr(obj, "api_token", None) or "").strip())
    if not token_value:
        messages.error(request, "تعذر إعادة تحميل الشاشة: لا يوجد token صالح.")
        return redirect("dashboard:screen_list")

    school_id = int(getattr(school, "id", 0) or 0)
    _queue_screen_reload(obj, school_id=school_id)

    logger.info(
        "screen_reload_now school_id=%s screen_id=%s",
        int(school_id),
        int(getattr(obj, "id", 0) or 0),
    )

    messages.success(request, f"تم إرسال أمر إعادة تحميل لهذه الشاشة ({obj.name}).")

    next_url = (request.POST.get("next") or "").strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect("dashboard:screen_list")


@manager_required
@require_POST
def screen_delete(request, pk: int):
    display_screen = _display_screen_model()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    obj = get_object_or_404(display_screen, pk=pk, school=school)
    obj.delete()
    messages.success(request, "تم حذف الشاشة.")
    return redirect("dashboard:screen_list")


@manager_required
@require_POST
def screen_unbind_device(request, pk: int):
    display_screen = _display_screen_model()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    obj = get_object_or_404(display_screen, pk=pk, school=school)

    try:
        display_screen._meta.get_field("bound_device_id")
        display_screen._meta.get_field("bound_at")
    except Exception:
        messages.error(request, "ميزة ربط الأجهزة غير متاحة حالياً.")
        return redirect("dashboard:screen_list")

    display_screen.objects.filter(pk=obj.pk).update(bound_device_id=None, bound_at=None)

    try:
        token_value = ((getattr(obj, "token", None) or getattr(obj, "api_token", None) or "").strip())
        if token_value:
            token_hash = hashlib.sha256(token_value.encode("utf-8")).hexdigest()
            cache.delete(f"display:token_map:{token_hash}")
    except Exception:
        pass

    messages.success(request, "تم فصل الجهاز. افتح الرابط على التلفاز الجديد ليتم ربطه تلقائياً.")
    return redirect("dashboard:screen_list")


@manager_required
def request_screen_addon(request):
    """زر/صفحة طلب زيادة شاشات: يفتح تذكرة دعم مُعبأة تلقائيًا."""
    display_screen = _display_screen_model()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    current_count = display_screen.objects.filter(school=school).count()
    max_screens = get_school_max_screens_limit(school)
    plan_name = get_school_effective_plan_label(school) or "—"

    school_name = (getattr(school, "name", "") or "").strip()
    subject = f"طلب زيادة شاشات - {school_name}" if school_name else "طلب زيادة شاشات"
    msg_lines = [
        f"المدرسة: {getattr(school, 'name', '')}",
        f"الخطة الحالية: {plan_name}",
        f"عدد الشاشات الحالية: {current_count}",
        f"الحد الحالي: {'غير محدود' if max_screens is None else int(max_screens)}",
        "",
        "المطلوب:",
        "- عدد الشاشات الإضافية: ",
        "- المدة: (شهر / نصف سنوي / سنوي)",
        "- ملاحظات: ",
    ]
    message_text = "\n".join(msg_lines)

    url = reverse("dashboard:customer_support_ticket_create")
    return redirect(f"{url}?subject={quote(subject)}&message={quote(message_text)}")

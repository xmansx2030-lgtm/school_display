from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import login
from django.conf import settings as django_settings
from django.core.cache import cache
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from core.models import DisplayScreen, SubscriptionPlan
from dashboard.access import get_active_school_or_redirect
from schedule.models import SchoolSettings
from subscriptions.plan_catalog import plan_cards
from .services import (
    TrialSignupError,
    activate_trial_if_eligible,
    check_trial_rate_limit,
    create_trial_signup,
    normalize_mobile,
)


THEME_MAP = {
    # legacy
    "default": "indigo",
    "boys": "emerald",
    "girls": "rose",

    # current
    "indigo": "indigo",
    "emerald": "emerald",
    "rose": "rose",
    "cyan": "cyan",
    "amber": "amber",
    "orange": "orange",
    "violet": "violet",
}


def health(request):
    return HttpResponse("School Display is running.")


def _abs_media_url(request, maybe_url: str | None) -> str | None:
    if not maybe_url:
        return None
    s = str(maybe_url).strip()
    if s.lower() in {"none", "null", "-"}:
        return None
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        return s
    try:
        return request.build_absolute_uri(s)
    except Exception:
        return s


def _resolve_screen_and_settings(
    key: str | None,
) -> tuple[DisplayScreen | None, SchoolSettings | None, str | None]:
    """
    key قد يكون:
    - token طويل (64)
    - أو short_code قصير (مثل 6)
    نُرجع دائمًا effective_token = screen.token حتى تعتمد الواجهة والـ API على token الحقيقي.
    """
    if not key:
        return None, None, None

    k = str(key).strip()
    if not k:
        return None, None, None

    screen = (
        DisplayScreen.objects.select_related("school")
        .filter(is_active=True)
        .filter(Q(token__iexact=k) | Q(short_code__iexact=k))
        .first()
    )
    if not screen:
        return None, None, None

    try:
        settings_obj = screen.school.schedule_settings
    except SchoolSettings.DoesNotExist:
        # نرجع token الحقيقي حتى لو الإعدادات ناقصة
        return screen, None, screen.token

    return screen, settings_obj, screen.token


def _build_display_context(request, key: str | None) -> dict | None:
    if not key:
        return None

    # ?nocache=1 مفيد أثناء التطوير
    preview_theme = (request.GET.get("preview_theme") or "").strip().lower()
    preview_accent = (request.GET.get("preview_accent") or "").strip()
    preview_lock = bool(preview_theme or preview_accent)
    bypass_cache = (request.GET.get("nocache") == "1") or preview_lock

    screen, settings_obj, effective_token = _resolve_screen_and_settings(key)
    if not screen or not settings_obj or not effective_token:
        return None

    try:
        schedule_revision = int(getattr(settings_obj, "schedule_revision", 0) or 0)
    except Exception:
        schedule_revision = 0

    # ✅ اربط الكاش بـ revision حتى ينعكس أي تحديث إعدادات فورًا.
    cache_key = f"display_ctx:{effective_token}:rev:{schedule_revision}"
    if not bypass_cache:
        cached = cache.get(cache_key)
        if cached:
            return cached

    # شعار
    logo_url = None
    if settings_obj.school and getattr(settings_obj.school, "logo", None):
        try:
            logo_url = settings_obj.school.logo.url
        except Exception:
            logo_url = None
    if not logo_url:
        logo_url = getattr(settings_obj, "logo_url", None)
    logo_url = _abs_media_url(request, logo_url)

    screen_theme_override = (getattr(screen, "theme_override", "") or "").strip()
    raw_theme = screen_theme_override or getattr(settings_obj, "theme", "default")
    theme = THEME_MAP.get(raw_theme, "indigo")

    # Preview overrides (non-persistent, per-request)
    if preview_theme:
        theme = THEME_MAP.get(preview_theme, theme)

    if isinstance(preview_accent, str):
        preview_accent = preview_accent.strip()
    else:
        preview_accent = ""
    if preview_accent and (len(preview_accent) != 7 or not preview_accent.startswith("#")):
        preview_accent = ""

    school_name = getattr(settings_obj, "name", None) or getattr(settings_obj.school, "name", "مدرستنا")
    school_type = getattr(settings_obj.school, "school_type", "") if getattr(settings_obj, "school", None) else ""
    display_accent_color = getattr(settings_obj, "display_accent_color", None)
    if preview_accent:
        display_accent_color = preview_accent

    ctx = {
        "screen": screen,
        "settings": settings_obj,
        "school_name": school_name,
        "school_type": school_type,
        "display_accent_color": display_accent_color,
        "logo_url": logo_url,
        "refresh_interval_sec": getattr(settings_obj, "refresh_interval_sec", 30),
        "ws_live_status_check_sec": int(
            getattr(django_settings, "DISPLAY_WS_LIVE_STATUS_CHECK_SEC", 60) or 60
        ),
        "standby_scroll_speed": getattr(settings_obj, "standby_scroll_speed", 0.8),
        "periods_scroll_speed": getattr(settings_obj, "periods_scroll_speed", 0.5),
        "now_hour": timezone.localtime().hour,
        "theme": theme,
        "theme_key": raw_theme,
        "screen_theme_override": screen_theme_override,
        "screen_occasion_theme": getattr(screen, "occasion_theme", "auto") or "auto",
        "screen_featured_panel": (
            getattr(screen, "featured_panel_override", "") or ""
        ),
        "screen_show_announcements": bool(getattr(screen, "show_announcements", True)),
        "screen_show_period_classes": bool(getattr(screen, "show_period_classes", True)),
        "screen_show_standby": bool(getattr(screen, "show_standby", True)),
        "screen_show_duty": bool(getattr(screen, "show_duty", True)),
        "screen_show_excellence": bool(getattr(screen, "show_excellence", True)),
        "preview_lock": preview_lock,
        # ✅ نعطي الواجهة token الحقيقي دائمًا
        "api_token": effective_token,
        "display_token": effective_token,
        "token": effective_token,
        "school_id": settings_obj.school_id if settings_obj.school_id else None,
        "screen_id": screen.pk,
        "schedule_revision": schedule_revision,
        # مهم: هذا هو المسار الذي يستدعيه display.js
        "snapshot_url": f"/api/display/snapshot/{effective_token}/",
    }

    cache.set(cache_key, ctx, 60)
    return ctx


@never_cache
def home(request):
    """
    الصفحة الرئيسية للشاشة:
    /?token=XXXX
    token قد يكون token طويل أو short_code بعد التحديث.
    """
    key = request.GET.get("token") or None
    ctx = _build_display_context(request, key)
    if not ctx:
        active_plans = list(
            SubscriptionPlan.objects.filter(is_active=True).order_by("sort_order", "price", "id")
        )
        landing_plans = plan_cards(active_plans)

        landing_trial = next(
            (plan for plan in landing_plans if plan["is_trial"]),
            {
                "name": "تجربة مجانية",
                "price": 0,
                "duration_days": 14,
                "duration_label": "14 يوماً",
                "is_trial": True,
                "is_free": True,
            },
        )
        return render(
            request,
            "website/unconfigured_display.html",
            {
                "token": key,
                "login_url": reverse("dashboard:login"),
                "trial_signup_url": reverse("website:trial_signup"),
                "plan_order_url": reverse("website:plan_order"),
                "dashboard_url": reverse("dashboard:index"),
                "landing_plans": landing_plans,
                "landing_trial": landing_trial,
            },
        )
    return render(request, "website/display.html", ctx)


def subscriptions(request):
    tamara_available = bool(
        getattr(django_settings, "TAMARA_ENABLED", False)
        and getattr(django_settings, "TAMARA_API_TOKEN", "")
    )
    return render(
        request,
        "website/subscriptions.html",
        {"tamara_available": tamara_available},
    )


@never_cache
@require_GET
def plan_order(request):
    """Resume a landing-page paid-plan journey after authentication."""
    plan_code = (request.GET.get("plan") or "").strip()
    plan = (
        SubscriptionPlan.objects.filter(code=plan_code, is_active=True, price__gt=0)
        .only("code", "name")
        .first()
    )
    if plan is None:
        messages.error(request, "الباقة المطلوبة غير متاحة حاليًا. اختر إحدى الباقات المتاحة.")
        return redirect(f'{reverse("website:home")}#pricing')

    order_path = f'{reverse("website:plan_order")}?{urlencode({"plan": plan.code})}'
    if not request.user.is_authenticated:
        login_url = reverse("dashboard:login")
        return redirect(f'{login_url}?{urlencode({"next": order_path})}')

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    trial_result = activate_trial_if_eligible(request.user, school)
    if trial_result.activated:
        messages.success(
            request,
            "تم تفعيل الباقة المجانية تلقائيًا. يمكنك الآن اختيار الباقة المدفوعة وإكمال الدفع.",
        )

    subscription_url = reverse("dashboard:my_subscription")
    return redirect(
        f'{subscription_url}?{urlencode({"plan": plan.code, "source": "landing"})}#renewal-section'
    )


@require_POST
@csrf_protect
def trial_signup(request):
    mobile = normalize_mobile(request.POST.get("mobile", ""))

    try:
        check_trial_rate_limit(request, mobile)
        result = create_trial_signup(request.POST)
    except TrialSignupError as exc:
        return JsonResponse({"ok": False, "errors": exc.errors}, status=400)
    except Exception:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "__all__": "حدث خطأ غير متوقع أثناء إنشاء التجربة. الرجاء المحاولة مرة أخرى."
                },
            },
            status=500,
        )

    login(request, result.user, backend="django.contrib.auth.backends.ModelBackend")

    requested_plan_code = (request.POST.get("plan_code") or "").strip()
    requested_plan = (
        SubscriptionPlan.objects.filter(
            code=requested_plan_code,
            is_active=True,
            price__gt=0,
        )
        .only("code")
        .first()
    )
    if requested_plan is not None:
        redirect_url = (
            f'{reverse("website:plan_order")}?'
            f'{urlencode({"plan": requested_plan.code})}'
        )
    else:
        redirect_url = reverse("dashboard:help_getting_started")

    return JsonResponse(
        {
            "ok": True,
            "message": "تم تجهيز تجربة المدرسة بنجاح.",
            "redirect_url": redirect_url,
            "login_url": reverse("dashboard:login"),
            "username": result.user.username,
            "mobile": getattr(getattr(result.user, "profile", None), "mobile", "") or mobile,
            "school_name": result.school.name,
        }
    )


def display(request):
    return home(request)


@never_cache
@xframe_options_sameorigin
def display_view(request, screen_key: str):
    """
    /display/<screen_key> (اختياري)
    screen_key قد يكون token أو short_code.
    """
    if not screen_key:
        raise Http404("Missing screen key.")

    ctx = _build_display_context(request, screen_key)
    if not ctx:
        raise Http404("Display is not configured or found.")

    return render(request, "website/display.html", ctx)


@never_cache
def short_display_redirect(request, short_code: str):
    """
    ✅ الرابط المختصر: /s/<short_code> أو /s/<short_code>/
    بعد التحديث لا نعمل redirect للرابط الطويل،
    بل نعرض الشاشة مباشرة (أفضل للتلفاز وأسهل للمستخدم).
    """
    code = (short_code or "").strip()
    if not code:
        raise Http404("Invalid short code.")
    return display_view(request, code)

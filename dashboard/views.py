from __future__ import annotations

from datetime import datetime, date, time, timedelta
import hashlib
import csv
import io
import math
import logging
import os
import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from django import forms
from django.apps import apps
from django.conf import settings as dj_settings
from django.contrib import messages
from django.contrib.auth import (
    login,
    get_user_model,
)
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied, FieldError
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Max, Q, Sum, Count
from django.db.models.functions import TruncMonth
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, NoReverseMatch, get_resolver
from django.templatetags.static import static as build_static_url
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

# Centralized access and permission policy. Aliases preserve the names used by
# legacy code while the implementation lives outside this view module.
from .access import (
    get_active_school_or_redirect,
    get_or_create_user_profile as _get_or_create_profile,
    safe_next_url as _safe_next_url,
)
from .auth_views import change_password, login_view, logout_view
from .decorators import manager_required, superuser_required, system_staff_required
from .support_views import (
    customer_support_ticket_create,
    customer_support_ticket_detail,
    customer_support_tickets,
    system_support_ticket_create,
    system_support_ticket_detail,
    system_support_tickets,
)
from .views_screens import (
    request_screen_addon,
    screen_create,
    screen_delete,
    screen_list,
    screen_refresh_now,
    screen_reload_now,
    screen_unbind_device,
)
from .forms import (
    SchoolSettingsForm,
    DayScheduleForm,
    LessonForm,
    AnnouncementForm,
    ExcellenceForm,
    StandbyForm,
    DutyAssignmentForm,
    SchoolSubscriptionForm,
    SystemUserCreateForm,
    SystemEmployeeCreateForm,
    SystemUserUpdateForm,
    PeriodFormSet,
    BreakFormSet,
    SubscriptionPlanForm,
    SubscriptionScreenAddonForm,
    SubscriptionRenewalRequestForm,
    SubscriptionNewRequestForm,
)
from core.models import SubscriptionPlan
from core.display_presence import display_is_live, latest_display_presence, live_display_count
from core.two_factor import get_enabled_config

logger = logging.getLogger(__name__)

from schedule.cache_utils import (
    bump_schedule_revision_for_school_id,
    bump_schedule_revision_for_school_id_debounced,
    get_schedule_revision_for_school_id,
    invalidate_display_snapshot_cache_for_school_id,
)
from schedule.time_engine import build_day_snapshot

if TYPE_CHECKING:
    pass

UserModel = get_user_model()

WEEKDAY_MAP = {
    1: "الاثنين",
    2: "الثلاثاء",
    3: "الأربعاء",
    4: "الخميس",
    5: "الجمعة",
    6: "السبت",
    7: "الأحد",
}

# أيام الأسبوع كاملة (الأحد → السبت) بترتيب الأسبوع السعودي
SCHOOL_WEEK = [
    (7, "الأحد"),
    (1, "الاثنين"),
    (2, "الثلاثاء"),
    (3, "الأربعاء"),
    (4, "الخميس"),
    (5, "الجمعة"),
    (6, "السبت"),
]
SCHOOL_WEEKDAY_IDS = [w for w, _ in SCHOOL_WEEK]

# أيام عطلة نهاية الأسبوع الافتراضية (تُنشأ بـ is_active=False)
WEEKEND_WEEKDAY_IDS = {5, 6}

# ترتيب العرض: الأحد أولاً ثم الاثنين … السبت
WEEKDAY_SORT = {7: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}


# ======================
# URL helpers (حل NoReverseMatch للـ namespaces)
# ======================

def _namespace_exists(ns: str) -> bool:
    try:
        return ns in (get_resolver().namespace_dict or {})
    except Exception:
        return False


def _safe_reverse(name: str, *, kwargs: dict | None = None, fallback: str | None = None) -> str:
    """
    reverse آمن: يرجع رابط '#' بدل كسر الصفحة عند NoReverseMatch
    """
    try:
        return reverse(name, kwargs=kwargs)
    except NoReverseMatch:
        if fallback:
            try:
                return reverse(fallback, kwargs=kwargs)
            except NoReverseMatch:
                return "#"
        return "#"


def _invalidate_display_cache(school, *, force_bump: bool = False):
    """
    Helper: Bump schedule revision, invalidate display cache, and broadcast
    WebSocket invalidation after any data change.
    Call this after saving/deleting lessons, announcements, excellence, standby, etc.
    """
    try:
        school_id = int(getattr(school, 'id', 0) or 0)
        if not school_id:
            return

        did_bump = bump_schedule_revision_for_school_id_debounced(school_id=school_id)
        if force_bump and not did_bump:
            forced_rev = bump_schedule_revision_for_school_id(school_id)
            did_bump = forced_rev is not None

        new_rev = int(get_schedule_revision_for_school_id(school_id) or 0)
        invalidate_display_snapshot_cache_for_school_id(school_id)

        # Force active screens to fetch once on next /status poll and clear stale
        # server-rendered display context cache keys.
        try:
            DisplayScreen = DisplayScreenModel()
            screens = DisplayScreen.objects.filter(school_id=school_id, is_active=True).only("token")
        except Exception:
            screens = []

        for screen in screens:
            token_value = (getattr(screen, "token", "") or "").strip()
            if not token_value:
                continue

            try:
                cache.delete(f"display_ctx:{token_value}")
                cache.delete(f"display_ctx:{token_value}:rev:{new_rev}")
            except Exception:
                pass

            try:
                token_hash = hashlib.sha256(token_value.encode("utf-8")).hexdigest()
                cache.set(f"display:force_refresh:{token_hash}", "1", timeout=120)
            except Exception:
                pass

        try:
            from schedule.signals import _broadcast_invalidate_ws
            transaction.on_commit(
                lambda _sid=school_id, _rev=new_rev: _broadcast_invalidate_ws(_sid, _rev)
            )
        except Exception:
            logger.warning(
                "WS broadcast import/schedule failed for school_id=%s", school_id
            )

        if not did_bump:
            logger.info(
                "schedule_revision debounce skip in dashboard invalidation school_id=%s force_bump=%s",
                school_id,
                bool(force_bump),
            )
    except Exception:
        logger.exception("_invalidate_display_cache failed for school=%s", school)


# ======================
# Model loader (حل ImportError نهائياً)
# ======================

@lru_cache(maxsize=128)
def _get_model(app_label: str, model_name: str):
    return apps.get_model(app_label, model_name)


def _get_model_first(*candidates: tuple[str, str]):
    """
    جرّب أكثر من مكان للموديل لتفادي تغيّر بنية التطبيقات.
    (مُثبت على هيكل مشروع school_display الحالي)
    """
    last_err = None
    for app_label, model_name in candidates:
        try:
            return _get_model(app_label, model_name)
        except Exception as e:
            last_err = e
    raise LookupError(
        f"تعذر العثور على الموديل من المرشحين: {candidates}. آخر خطأ: {last_err}"
    )


@lru_cache(maxsize=32)
def SchoolModel():
    return _get_model_first(("core", "School"))


@lru_cache(maxsize=32)
def UserProfileModel():
    return _get_model_first(("core", "UserProfile"))


@lru_cache(maxsize=32)
def SchoolSettingsModel():
    return _get_model_first(("schedule", "SchoolSettings"))


@lru_cache(maxsize=32)
def SchoolClassModel():
    return _get_model_first(("schedule", "SchoolClass"))


@lru_cache(maxsize=32)
def SubjectModel():
    return _get_model_first(("schedule", "Subject"))


@lru_cache(maxsize=32)
def TeacherModel():
    return _get_model_first(("schedule", "Teacher"))


@lru_cache(maxsize=32)
def DayScheduleModel():
    return _get_model_first(("schedule", "DaySchedule"))


@lru_cache(maxsize=32)
def PeriodModel():
    return _get_model_first(("schedule", "Period"))


@lru_cache(maxsize=32)
def BreakModel():
    return _get_model_first(("schedule", "Break"))


@lru_cache(maxsize=32)
def ClassLessonModel():
    return _get_model_first(("schedule", "ClassLesson"))


@lru_cache(maxsize=32)
def AnnouncementModel():
    return _get_model_first(("notices", "Announcement"))


@lru_cache(maxsize=32)
def ExcellenceModel():
    return _get_model_first(("notices", "Excellence"))


@lru_cache(maxsize=32)
def StandbyAssignmentModel():
    return _get_model_first(("standby", "StandbyAssignment"))


@lru_cache(maxsize=32)
def DutyAssignmentModel():
    return _get_model_first(("schedule", "DutyAssignment"))


@lru_cache(maxsize=32)
def DisplayScreenModel():
    return _get_model_first(("core", "DisplayScreen"))


# ======================
# Helpers
# ======================

def _model_has_field(model, field_name: str) -> bool:
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _join_unique_msgs(msgs: list[str]) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for m in msgs:
        m = (m or "").strip()
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)
    return " | ".join(ordered)


def _collect_form_errors(*forms_or_formsets) -> str:
    msgs: list[str] = []
    for obj in forms_or_formsets:
        if obj is None:
            continue

        # Form
        if hasattr(obj, "errors") and hasattr(obj, "fields"):
            if obj.errors:
                for field, errs in obj.errors.items():
                    if field == "__all__":
                        for e in errs:
                            msgs.append(str(e))
                    else:
                        label = obj.fields.get(field).label if field in obj.fields else field
                        for e in errs:
                            msgs.append(f"{label}: {e}")

        # FormSet
        if hasattr(obj, "forms") and hasattr(obj, "non_form_errors"):
            for f in getattr(obj, "forms", []):
                if getattr(f, "errors", None):
                    for field, errs in f.errors.items():
                        if field == "__all__":
                            for e in errs:
                                msgs.append(str(e))
                        else:
                            label = f.fields.get(field).label if field in f.fields else field
                            for e in errs:
                                msgs.append(f"{label}: {e}")
            for e in obj.non_form_errors():
                msgs.append(str(e))

    return _join_unique_msgs(msgs)


def _to_int(val, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return int(default)


def _rev_manager(obj, preferred: str, fallback: str):
    mgr = getattr(obj, preferred, None)
    if mgr is None:
        mgr = getattr(obj, fallback, None)
    return mgr


def _parse_hhmm_or_hhmmss(s: str) -> time:
    raw = (s or "").strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    raise ValueError("صيغة الوقت غير صحيحة. استخدم HH:MM أو HH:MM:SS.")


def _time_to_hhmm(value: time | None) -> str:
    if value is None:
        return ""
    return value.strftime("%H:%M")


def _minutes_diff_wrap(start_t: time, end_t: time) -> int:
    start_total = (start_t.hour * 60) + start_t.minute
    end_total = (end_t.hour * 60) + end_t.minute
    diff = end_total - start_total
    if diff < 0:
        diff += 24 * 60
    return diff


def _build_day_autofill_seed(day) -> dict[str, Any]:
    seed: dict[str, Any] = {
        "target_periods_count": int(getattr(day, "periods_count", 0) or 0),
        "start_time": "",
        "period_minutes": "",
        "gap_minutes": "",
        "break_after": "",
        "break_minutes": "",
        "has_actual_values": False,
    }

    periods_mgr = _rev_manager(day, "periods", "period_set")
    breaks_mgr = _rev_manager(day, "breaks", "break_set")

    periods = list(periods_mgr.all()) if periods_mgr is not None else []
    periods.sort(key=lambda p: (int(getattr(p, "index", 0) or 0), getattr(p, "starts_at", time.min)))

    breaks = list(breaks_mgr.all().order_by("starts_at")) if breaks_mgr is not None else []

    if not periods:
        return seed

    seed["has_actual_values"] = True

    first_period = periods[0]
    seed["start_time"] = _time_to_hhmm(getattr(first_period, "starts_at", None))

    durations = [
        _minutes_diff_wrap(p.starts_at, p.ends_at)
        for p in periods
        if getattr(p, "starts_at", None) and getattr(p, "ends_at", None)
    ]
    if durations:
        seed["period_minutes"] = max(1, int(durations[0]))

    intervals: list[int] = []
    for idx in range(len(periods) - 1):
        current_end = getattr(periods[idx], "ends_at", None)
        next_start = getattr(periods[idx + 1], "starts_at", None)
        if current_end and next_start:
            intervals.append(_minutes_diff_wrap(current_end, next_start))

    if breaks:
        first_break = breaks[0]
        seed["break_minutes"] = int(getattr(first_break, "duration_min", 0) or 0)

        break_after = 0
        for pos, period in enumerate(periods, start=1):
            if getattr(period, "ends_at", None) == getattr(first_break, "starts_at", None):
                break_after = pos
                break
        if break_after == 0:
            best_idx = 0
            best_gap = None
            b_start = getattr(first_break, "starts_at", None)
            if b_start:
                for pos, period in enumerate(periods, start=1):
                    end_t = getattr(period, "ends_at", None)
                    if end_t is None:
                        continue
                    gap_val = _minutes_diff_wrap(end_t, b_start)
                    if best_gap is None or gap_val < best_gap:
                        best_gap = gap_val
                        best_idx = pos
            break_after = best_idx
        seed["break_after"] = int(max(0, break_after))
    else:
        seed["break_after"] = 0
        seed["break_minutes"] = 0

    if intervals:
        gap_candidates = [max(0, int(v)) for v in intervals]
        break_after = int(seed.get("break_after") or 0)
        break_minutes = int(seed.get("break_minutes") or 0)
        if break_minutes > 0 and 1 <= break_after <= len(intervals):
            adjusted_gap = intervals[break_after - 1] - break_minutes
            gap_candidates.append(max(0, int(adjusted_gap)))
        seed["gap_minutes"] = min(gap_candidates) if gap_candidates else 0
    else:
        seed["gap_minutes"] = 0

    return seed


def _classes_qs_from_settings(settings_obj):
    """
    بعض المشاريع تسمي علاقة الفصول: settings.school_classes
    وبعضها: settings.classes أو schoolclass_set
    نخليها مرنة حتى لا تتكسر الصفحات.
    """
    SchoolClass = SchoolClassModel()
    if settings_obj is None:
        return SchoolClass.objects.none()

    for preferred, fallback in (("school_classes", "schoolclass_set"), ("classes", "schoolclass_set")):
        mgr = _rev_manager(settings_obj, preferred, fallback)
        if mgr is not None:
            try:
                return mgr.all()
            except Exception:
                continue

    try:
        return SchoolClass.objects.filter(settings=settings_obj)
    except Exception:
        return SchoolClass.objects.none()


def _dashboard_help_image(step_key: str, fallback_path: str) -> str:
    screenshot_dir = os.path.join("img", "dashboard-help", "real")
    candidate_names = [
        f"{step_key}.png",
        f"{step_key}.webp",
        f"{step_key}.jpg",
        f"{step_key}.jpeg",
    ]

    search_roots: list[str] = []
    for static_dir in getattr(dj_settings, "STATICFILES_DIRS", []) or []:
        search_roots.append(str(static_dir))

    base_static_dir = os.path.join(str(getattr(dj_settings, "BASE_DIR", "")), "static")
    if base_static_dir:
        search_roots.append(base_static_dir)

    seen_roots: set[str] = set()
    for root in search_roots:
        normalized_root = os.path.normpath(root)
        if normalized_root in seen_roots:
            continue
        seen_roots.add(normalized_root)

        for filename in candidate_names:
            abs_path = os.path.join(normalized_root, screenshot_dir, filename)
            try:
                if os.path.exists(abs_path):
                    rel_path = "/".join(["img", "dashboard-help", "real", filename])
                    return build_static_url(rel_path)
            except Exception:
                continue

    return build_static_url(fallback_path)


def _build_dashboard_onboarding_context(request, school, settings_obj=None):
    SchoolSettings = SchoolSettingsModel()
    Subject = SubjectModel()
    Teacher = TeacherModel()
    Period = PeriodModel()
    DisplayScreen = DisplayScreenModel()

    settings_obj = settings_obj or SchoolSettings.objects.filter(school=school).first()

    screens_qs = DisplayScreen.objects.filter(school=school)
    screens_count = screens_qs.count()
    launch_screen = screens_qs.filter(is_active=True).order_by("id").first()
    launch_screen_url = (
        reverse("website:short_display", args=[launch_screen.short_code])
        if launch_screen is not None and getattr(launch_screen, "short_code", "")
        else reverse("dashboard:screen_list")
    )
    classes_count = _classes_qs_from_settings(settings_obj).count() if settings_obj else 0
    subjects_count = Subject.objects.filter(school=school).count()
    teachers_count = Teacher.objects.filter(school=school).count()
    periods_count = Period.objects.filter(day__settings__school=school).count()

    theme_value = (getattr(settings_obj, "theme", "") or "").strip()
    accent_value = (getattr(settings_obj, "display_accent_color", "") or "").strip()
    featured_panel_value = (getattr(settings_obj, "featured_panel", "") or "").strip()
    featured_panel_label = dict(getattr(SchoolSettings, "FEATURE_PANEL_CHOICES", [])).get(
        featured_panel_value,
        "لوحة الشرف",
    )
    settings_customized = bool(accent_value or (theme_value and theme_value != "default"))

    def _step_status(is_complete: bool, *, optional: bool = False):
        if is_complete:
            return ("مكتمل", "emerald")
        if optional:
            return ("اختياري", "amber")
        return ("ابدأ الآن", "slate")

    screens_status, screens_tone = _step_status(screens_count > 0)
    data_status, data_tone = _step_status(classes_count > 0 and subjects_count > 0 and teachers_count > 0)
    schedule_status, schedule_tone = _step_status(periods_count > 0)
    settings_status, settings_tone = _step_status(settings_customized, optional=True)

    setup_steps = [
        {
            "key": "screens",
            "title": "إنشاء شاشة عرض",
            "eyebrow": "الخطوة 1",
            "description": "أنشئ شاشة جديدة لتحصل على رابط التشغيل الذي ستفتحه على التلفاز أو الشاشة الذكية داخل المدرسة.",
            "image_url": _dashboard_help_image("screens", "img/dashboard-help/create-screen.svg"),
            "cta_url": reverse("dashboard:screen_list"),
            "cta_label": "الذهاب إلى الشاشات",
            "metric_value": str(screens_count),
            "metric_label": "شاشة مسجلة",
            "status_label": screens_status,
            "status_tone": screens_tone,
            "is_complete": screens_count > 0,
            "required": True,
            "tips": [
                "اضغط على شاشة جديدة ثم احفظ الشاشة.",
                "انسخ الرابط المختصر وافتحه على التلفاز.",
                "إذا أردت نقلها لجهاز آخر استخدم فك ارتباط الجهاز أولاً.",
            ],
        },
        {
            "key": "school-data",
            "title": "إدخال بيانات الفصول والمواد والمعلمين",
            "eyebrow": "الخطوة 2",
            "description": "جهّز البنية الأساسية للجدول بإضافة الفصول ثم المواد ثم المعلمين أو المعلمات من صفحة واحدة.",
            "image_url": _dashboard_help_image("school-data", "img/dashboard-help/school-data.svg"),
            "cta_url": reverse("dashboard:school_data"),
            "cta_label": "إدارة بيانات المدرسة",
            "metric_value": f"{classes_count}/{subjects_count}/{teachers_count}",
            "metric_label": "فصول / مواد / معلمون",
            "status_label": data_status,
            "status_tone": data_tone,
            "is_complete": classes_count > 0 and subjects_count > 0 and teachers_count > 0,
            "required": True,
            "tips": [
                "ابدأ بالفصول لأن الجدول يعتمد عليها.",
                "أضف المواد الرئيسية قبل توزيع الحصص اليومية.",
                "أدخل المعلمين بنفس الأسماء التي تريد ظهورها على الشاشة.",
            ],
        },
        {
            "key": "schedule",
            "title": "ضبط توقيت الحصص والأيام",
            "eyebrow": "الخطوة 3",
            "description": "حدد أيام الدراسة وأوقات بداية ونهاية الحصص، ثم افتح الجدول اليومي لتوزيع المادة والمعلم على كل حصة.",
            "image_url": _dashboard_help_image("schedule", "img/dashboard-help/day-schedule.svg"),
            "cta_url": reverse("dashboard:days_list"),
            "cta_label": "ضبط الأيام والحصص",
            "metric_value": str(periods_count),
            "metric_label": "حصة مضبوطة",
            "status_label": schedule_status,
            "status_tone": schedule_tone,
            "is_complete": periods_count > 0,
            "required": True,
            "tips": [
                "فعّل الأيام الدراسية وألغِ أيام الإجازة.",
                "من داخل كل يوم اضبط عدد الحصص والأوقات.",
                "بعد ذلك افتح الجدول اليومي لإسناد المادة والمعلم لكل توقيت.",
            ],
        },
        {
            "key": "settings",
            "title": "اختيار لون الثيم والكرت المميز",
            "eyebrow": "إعداد إضافي",
            "description": "من صفحة الإعدادات تستطيع تخصيص لون الثيم واختيار الكرت البارز في الشاشة بين لوحة التميز أو الإشراف والمناوبة.",
            "image_url": _dashboard_help_image("settings", "img/dashboard-help/theme-settings.svg"),
            "cta_url": reverse("dashboard:settings"),
            "cta_label": "فتح الإعدادات",
            "metric_value": featured_panel_label,
            "metric_label": "الكرت الحالي",
            "status_label": settings_status,
            "status_tone": settings_tone,
            "is_complete": settings_customized,
            "required": False,
            "tips": [
                "اختر اللون الذي يناسب هوية المدرسة بصريًا.",
                "حدد هل الكرت المميز هو التميز أو الإشراف والمناوبة.",
                "أي تعديل هنا ينعكس مباشرة على شاشة العرض.",
            ],
        },
    ]

    required_steps = [step for step in setup_steps if step["required"]]
    completed_required = sum(1 for step in required_steps if step["is_complete"])
    total_required = len(required_steps)
    progress_percent = int(round((completed_required / total_required) * 100)) if total_required else 0
    next_step = next((step for step in required_steps if not step["is_complete"]), None)

    return {
        "setup_steps": setup_steps,
        "setup_summary": {
            "completed_required": completed_required,
            "total_required": total_required,
            "progress_percent": progress_percent,
            "next_step_title": next_step["title"] if next_step else "تم تجهيز الأساسيات",
            "next_step_url": next_step["cta_url"] if next_step else launch_screen_url,
            "next_step_cta_label": next_step["cta_label"] if next_step else "افتح شاشة مدرستك الآن",
            "launch_screen_url": launch_screen_url,
            "guide_url": reverse("dashboard:help_getting_started"),
        },
    }


# ======================
# مصادقة ولوحة المدير
# ======================

def demo_login(request):
    if not getattr(dj_settings, "DEBUG", False):
        from django.http import Http404
        raise Http404

    School = SchoolModel()
    DEMO_ID = "demo_user"
    DEMO_SCHOOL_SLUG = "demo-school"

    demo_school, _ = School.objects.get_or_create(
        slug=DEMO_SCHOOL_SLUG,
        defaults={"name": "مدرسة تجريبية", "is_active": True},
    )

    lookup = {}
    defaults: dict[str, Any] = {"is_active": True}
    if _model_has_field(UserModel, "username"):
        lookup["username"] = DEMO_ID
        defaults.update({"first_name": "حساب", "last_name": "تجريبي", "email": "demo@example.com"})
    elif _model_has_field(UserModel, "phone"):
        lookup["phone"] = "0500000000"
        defaults.update({"name": "حساب تجريبي"})
    else:
        lookup["email"] = "demo@example.com"
        defaults.update({"first_name": "Demo", "last_name": "User"})

    demo_user, created = UserModel.objects.get_or_create(**lookup, defaults=defaults)
    if created:
        demo_user.set_password(get_random_string(12))
        demo_user.save()

    profile = _get_or_create_profile(demo_user)
    if demo_school not in profile.schools.all():
        profile.schools.add(demo_school)
    if profile.active_school != demo_school:
        profile.active_school = demo_school
        profile.save(update_fields=["active_school"])

    login(request, demo_user, backend="django.contrib.auth.backends.ModelBackend")
    messages.success(request, "تم تسجيل دخولك بحساب تجريبي. البيانات هنا لأغراض العرض فقط.")
    return redirect("dashboard:index")


@manager_required
def index(request):
    Announcement = AnnouncementModel()
    Excellence = ExcellenceModel()
    StandbyAssignment = StandbyAssignmentModel()
    SchoolSettings = SchoolSettingsModel()
    DisplayScreen = DisplayScreenModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    if school is None:
        return render(request, "dashboard/no_school.html")

    today = timezone.localdate()
    now = timezone.now()
    screens_qs = DisplayScreen.objects.filter(school=school).order_by("id")
    screens = list(screens_qs)
    live_screens_count = live_display_count(screens, now=now)
    active_screens_count = sum(1 for screen in screens if bool(getattr(screen, "is_active", False)))

    primary_screen = next(
        (screen for screen in screens if bool(getattr(screen, "is_active", False))),
        screens[0] if screens else None,
    )
    primary_screen_is_live = bool(primary_screen and display_is_live(primary_screen, now=now))
    primary_screen_is_bound = bool(
        primary_screen and (getattr(primary_screen, "bound_device_id", "") or "").strip()
    )
    primary_screen_last_seen = latest_display_presence(primary_screen) if primary_screen else None

    if primary_screen is None:
        screen_hub = {
            "exists": False,
            "status": "لا توجد شاشة بعد",
            "tone": "new",
            "detail": "أنشئ شاشة واحدة لتحصل على رابط تشغيل جاهز للتلفاز أو الشاشة الذكية.",
            "primary_label": "أنشئ أول شاشة",
            "primary_url": reverse("dashboard:screen_list"),
            "short_code": "",
        }
    else:
        primary_screen_url = (
            reverse("website:short_display", args=[primary_screen.short_code])
            if getattr(primary_screen, "short_code", "")
            else reverse("dashboard:screen_list")
        )
        if not bool(getattr(primary_screen, "is_active", False)):
            status = "الشاشة موقوفة"
            tone = "paused"
            detail = "فعّل الشاشة من صفحة إدارة الشاشات حتى تستقبل الجدول والمحتوى من جديد."
        elif primary_screen_is_live:
            status = "متصلة الآن"
            tone = "live"
            detail = "الشاشة تعمل وتستقبل تحديثات الجدول والتنبيهات تلقائيًا."
        elif primary_screen_is_bound and primary_screen_last_seen:
            status = "غير متصلة حاليًا"
            tone = "offline"
            detail = "الشاشة مرتبطة بجهاز، لكن لم يصل منها اتصال حديث. تحقق من تشغيل التلفاز والإنترنت."
        elif primary_screen_is_bound:
            status = "مرتبطة وجاهزة"
            tone = "waiting"
            detail = "تم ربط الجهاز بنجاح، وسيظهر الاتصال اللحظي عند وصول أول نبض تشغيل."
        else:
            status = "بانتظار أول تشغيل"
            tone = "waiting"
            detail = "افتح رابط الشاشة على التلفاز مرة واحدة لإكمال الربط وبدء العرض."

        screen_hub = {
            "exists": True,
            "name": primary_screen.name,
            "status": status,
            "tone": tone,
            "detail": detail,
            "primary_label": "فتح شاشة المدرسة",
            "primary_url": primary_screen_url,
            "short_code": getattr(primary_screen, "short_code", "") or "",
        }

    stats = {
        "ann_count": Announcement.objects.filter(school=school).count(),
        "exc_count": Excellence.objects.filter(school=school).count(),
        "standby_today": StandbyAssignment.objects.filter(school=school, date=today).count(),
        "screens_count": len(screens),
        "active_screens_count": active_screens_count,
        "live_screens_count": live_screens_count,
    }

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    day_snapshot = {}
    current_state = {}
    current_period = None
    next_period = None
    if settings_obj:
        try:
            day_snapshot = build_day_snapshot(settings_obj)
            current_state = day_snapshot.get("state") or {}
            current_period = day_snapshot.get("current_period")
            next_period = day_snapshot.get("next_period")
        except Exception:
            logger.exception("dashboard index day snapshot failed school_id=%s", getattr(school, "id", None))
            day_snapshot = {}
            current_state = {}

    today_weekday = today.isoweekday()
    today_weekday_label = WEEKDAY_MAP.get(today_weekday, "")
    schedule_revision = int(getattr(settings_obj, "schedule_revision", 0) or 0) if settings_obj else 0

    SubModel = _get_subscription_model()
    subscription = None
    if SubModel is not None:
        subscription = SubModel.objects.filter(school=school).order_by("-starts_at", "-id").first()

    onboarding_context = _build_dashboard_onboarding_context(request, school, settings_obj=settings_obj)

    return render(
        request,
        "dashboard/index.html",
        {
            "stats": stats,
            "settings": settings_obj,
            "subscription": subscription,
            "today": today,
            "today_weekday_label": today_weekday_label,
            "day_snapshot": day_snapshot,
            "current_state": current_state,
            "current_period": current_period,
            "next_period": next_period,
            "schedule_revision": schedule_revision,
            "screen_hub": screen_hub,
            **onboarding_context,
        },
    )


@manager_required
def help_getting_started(request):
    SchoolSettings = SchoolSettingsModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    onboarding_context = _build_dashboard_onboarding_context(request, school, settings_obj=settings_obj)

    try:
        profile = request.user.profile
        if profile.needs_onboarding:
            profile.needs_onboarding = False
            profile.save(update_fields=["needs_onboarding"])
    except Exception:
        pass

    return render(
        request,
        "dashboard/help_getting_started.html",
        {
            "school": school,
            "settings": settings_obj,
            **onboarding_context,
        },
    )


@login_required
def select_school(request):
    profile = _get_or_create_profile(request.user)

    if getattr(request.user, "is_superuser", False):
        return redirect("dashboard:system_admin_dashboard")

    schools_qs = getattr(profile, "schools", None)
    if schools_qs is None:
        return render(request, "dashboard/no_school.html")

    schools = profile.schools.order_by("name", "id")
    if not schools.exists():
        return render(request, "dashboard/no_school.html")

    if request.method == "POST":
        sid = (request.POST.get("school_id") or "").strip()
        try:
            school = profile.schools.get(pk=int(sid))
        except Exception:
            messages.error(request, "المدرسة غير موجودة أو ليست ضمن صلاحياتك.")
            return redirect("dashboard:select_school")

        profile.active_school = school
        profile.save(update_fields=["active_school"])
        messages.success(request, f"تم اختيار المدرسة النشطة: {school.name}")

        return redirect(_safe_next_url(request, default_name="dashboard:index"))

    return render(
        request,
        "dashboard/select_school.html",
        {"schools": schools, "active_school_id": getattr(profile, "active_school_id", None)},
    )


# ======================
# إعدادات المدرسة
# ======================

@never_cache
@manager_required
def school_settings(request):
    SchoolSettings = SchoolSettingsModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    obj, _created = SchoolSettings.objects.get_or_create(
        school=school,
        defaults={"name": school.name},
    )

    # Preview: use latest active screen short_code if exists
    preview_url = None
    try:
        from core.models import DisplayScreen  # local import

        scr = (
            DisplayScreen.objects
            .filter(school=school, is_active=True)
            .exclude(short_code__isnull=True)
            .exclude(short_code__exact="")
            .order_by("-id")
            .first()
        )
        if scr:
            preview_url = f"/s/{scr.short_code}/"
    except Exception:
        preview_url = None

    if request.method == "POST":
        form = SchoolSettingsForm(request.POST, request.FILES, instance=obj, user=request.user)
        if form.is_valid():
            form.save()
            
            # ✅ Invalidate display cache + WS broadcast so TV updates immediately
            _invalidate_display_cache(obj.school or school, force_bump=True)
            
            messages.success(request, "تم حفظ إعدادات المدرسة.")
            return redirect("dashboard:settings")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = SchoolSettingsForm(instance=obj, user=request.user)

    return render(
        request,
        "dashboard/settings.html",
        {
            "form": form,
            "display_preview_url": preview_url,
            "school": school,
            "two_factor_enabled": get_enabled_config(request.user) is not None,
        },
    )


# ======================
# إدارة أيام الجدول
# ======================

@manager_required
def days_list(request):
    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.warning(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    existing = set(
        DaySchedule.objects.filter(settings=settings_obj, weekday__in=SCHOOL_WEEKDAY_IDS)
        .values_list("weekday", flat=True)
    )

    for w in SCHOOL_WEEKDAY_IDS:
        if w not in existing:
            DaySchedule.objects.create(
                settings=settings_obj,
                weekday=w,
                periods_count=7 if w in (7, 1) else 6,
                is_active=(w not in WEEKEND_WEEKDAY_IDS),
            )

    days = list(
        DaySchedule.objects.filter(settings=settings_obj, weekday__in=SCHOOL_WEEKDAY_IDS)
        .prefetch_related("periods", "breaks")
    )
    days.sort(key=lambda d: WEEKDAY_SORT.get(d.weekday, 99))

    total_periods = 0
    active_days_count = 0
    holiday_days_count = 0
    for d in days:
        d.day_name = WEEKDAY_MAP.get(d.weekday, str(d.weekday))
        d.breaks_count = d.breaks.count()
        periods = sorted(d.periods.all(), key=lambda p: p.starts_at)
        if periods:
            d.first_period_time = periods[0].starts_at.strftime("%H:%M")
            d.last_period_time = periods[-1].ends_at.strftime("%H:%M")
            start_dt = datetime.combine(date.today(), periods[0].starts_at)
            end_dt = datetime.combine(date.today(), periods[-1].ends_at)
            diff = end_dt - start_dt
            hours, remainder = divmod(diff.seconds, 3600)
            minutes = remainder // 60
            d.total_duration = f"{hours:02d}:{minutes:02d}"
        else:
            d.first_period_time = "--:--"
            d.last_period_time = "--:--"
            d.total_duration = "--:--"
        if d.is_active:
            active_days_count += 1
            total_periods += d.periods_count or 0
        else:
            holiday_days_count += 1

    active_days = [d for d in days if d.is_active]
    avg_periods = total_periods / active_days_count if active_days_count else 0
    max_periods_day = max(active_days, key=lambda d: d.periods_count) if active_days else None
    min_periods_day = min(active_days, key=lambda d: d.periods_count) if active_days else None
    last_updated = timezone.localtime().strftime("%H:%M")
    avg_periods_display = f"{avg_periods:.1f}"

    return render(
        request,
        "dashboard/days_list.html",
        {
            "days": days,
            "total_periods": total_periods,
            "avg_periods": avg_periods,
            "avg_periods_display": avg_periods_display,
            "active_days_count": active_days_count,
            "holiday_days_count": holiday_days_count,
            "max_periods_day": max_periods_day,
            "min_periods_day": min_periods_day,
            "last_updated": last_updated,
        },
    )


@manager_required
@transaction.atomic
def day_edit(request, weekday: int):
    # Backward compatibility: older dashboards used Sunday=0
    if weekday == 0:
        weekday = 7
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "رقم اليوم غير صالح.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.warning(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day = get_object_or_404(DaySchedule, settings=settings_obj, weekday=weekday)
    day.day_name = WEEKDAY_MAP[weekday]

    if request.method == "POST":
        form = DayScheduleForm(request.POST, instance=day)
        p_formset = PeriodFormSet(request.POST, instance=day, prefix="p")
        b_formset = BreakFormSet(request.POST, instance=day, prefix="b")

        if form.is_valid() and p_formset.is_valid() and b_formset.is_valid():
            form.save()
            p_formset.save()
            b_formset.save()
            
            # ✅ Invalidate display cache + WS broadcast
            _invalidate_display_cache(school)
            
            messages.success(request, "تم حفظ جدول اليوم بنجاح.")
            return redirect("dashboard:days_list")

        detail = _collect_form_errors(form, p_formset, b_formset)
        if not detail:
            detail = "تحقق من الأوقات: يوجد حقول ناقصة/مكررة أو تداخلات زمنية."
        messages.error(request, detail)
    else:
        form = DayScheduleForm(instance=day)
        p_formset = PeriodFormSet(instance=day, prefix="p")
        b_formset = BreakFormSet(instance=day, prefix="b")

    return render(
        request,
        "dashboard/day_edit.html",
        {
            "day": day,
            "form": form,
            "p_formset": p_formset,
            "b_formset": b_formset,
            "autofill_seed": _build_day_autofill_seed(day),
        },
    )


@manager_required
@transaction.atomic
@require_POST
def day_autofill(request, weekday: int):
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "رقم اليوم غير صالح.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day = get_object_or_404(DaySchedule, settings=settings_obj, weekday=weekday)

    try:
        def _required_int_param(name: str, label: str, *, min_value: int) -> int:
            raw = (request.POST.get(name) or "").strip()
            if raw == "":
                raise ValueError(f"حقل '{label}' مطلوب.")
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"قيمة '{label}' غير صالحة.")
            if value < min_value:
                raise ValueError(f"قيمة '{label}' يجب أن تكون {min_value} أو أكثر.")
            return value

        start_time_str = (request.POST.get("start_time") or "").strip()
        if not start_time_str:
            raise ValueError("حقل 'بداية اليوم' مطلوب.")

        new_count = _required_int_param("target_periods_count", "عدد الحصص المطلوب", min_value=1)
        period_minutes = _required_int_param("period_minutes", "زمن الحصة", min_value=1)
        gap_minutes = _required_int_param("gap_minutes", "فاصل بين الحصص", min_value=0)
        break_after = _required_int_param("break_after", "الفسحة بعد حصة رقم", min_value=0)
        break_minutes = _required_int_param("break_minutes", "مدة الفسحة", min_value=0)

        day.periods_count = new_count
        day.save(update_fields=["periods_count"])

        period_seconds = 0
        gap_seconds = 0
        break_seconds = 0

        start_t = _parse_hhmm_or_hhmmss(start_time_str)

        p_len = timedelta(minutes=period_minutes, seconds=period_seconds)
        gap = timedelta(minutes=gap_minutes, seconds=gap_seconds)
        brk = timedelta(minutes=break_minutes, seconds=break_seconds)

        if p_len.total_seconds() <= 0:
            raise ValueError("طول الحصة يجب أن يكون أكبر من صفر.")

        if not day.periods_count or day.periods_count <= 0:
            messages.error(request, "عدد الحصص لليوم يساوي صفر.")
            return redirect("dashboard:day_edit", weekday=weekday)

        if break_after < 0 or break_after > day.periods_count:
            raise ValueError("قيمة 'الفسحة بعد الحصة رقم' خارج النطاق.")

        periods_mgr = _rev_manager(day, "periods", "period_set")
        breaks_mgr = _rev_manager(day, "breaks", "break_set")
        if periods_mgr is None or breaks_mgr is None:
            raise ValueError("تعذر الوصول لعلاقات الحصص/الفسح (تحقق من related_name).")

        base_date = timezone.localdate()
        cursor = datetime.combine(base_date, start_t)

        periods_mgr.all().delete()
        breaks_mgr.all().delete()

        break_minutes_final = (
            int(math.ceil(max(0, brk.total_seconds()) / 60.0))
            if brk.total_seconds() > 0
            else 0
        )

        for i in range(1, day.periods_count + 1):
            start_period = cursor
            end_period = cursor + p_len
            periods_mgr.create(index=i, starts_at=start_period.time(), ends_at=end_period.time())
            cursor = end_period

            if break_minutes_final > 0 and break_after == i:
                breaks_mgr.create(label="فسحة", starts_at=cursor.time(), duration_min=break_minutes_final)
                cursor += timedelta(minutes=break_minutes_final)

            cursor += gap

        # ✅ Invalidate display cache + WS broadcast
        _invalidate_display_cache(school)

        messages.success(request, "تمت التعبئة التلقائية للجدول.")
        return redirect("dashboard:day_edit", weekday=weekday)

    except Exception as e:
        logger.exception("day_autofill failed")
        messages.error(request, f"تعذّر تنفيذ التعبئة: {e}")
        return redirect("dashboard:day_edit", weekday=weekday)


@manager_required
@require_POST
def day_toggle(request, weekday: int):
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "اليوم غير صالح.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day, _ = DaySchedule.objects.get_or_create(settings=settings_obj, weekday=weekday)
    day.is_active = not bool(getattr(day, "is_active", True))
    day.save(update_fields=["is_active"])

    # ✅ Invalidate display cache + WS broadcast
    _invalidate_display_cache(school)

    status = "تفعيل" if day.is_active else "تعطيل"
    messages.success(request, f"تم {status} يوم {WEEKDAY_MAP.get(weekday, str(weekday))}.")
    return redirect("dashboard:days_list")


# ======================
# التنبيهات وبطاقات التميز
# ======================

@manager_required
def ann_list(request):
    Announcement = AnnouncementModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    qs = Announcement.objects.filter(school=school).order_by("-starts_at", "-id")
    now = timezone.now()
    active_q = (
        Q(is_active=True)
        & (Q(starts_at__lte=now) | Q(starts_at__isnull=True))
        & (Q(expires_at__gt=now) | Q(expires_at__isnull=True))
    )
    future_q = Q(is_active=True) & Q(starts_at__gt=now)

    active_count = qs.filter(active_q).count()
    future_count = qs.filter(future_q).count()
    total_count = qs.count()
    expired_count = max(total_count - active_count - future_count, 0)

    page = Paginator(qs, 10).get_page(request.GET.get("page"))
    for ann in page.object_list:
        if ann.active_now:
            ann.dashboard_status_key = "active"
            ann.dashboard_status_label = "فعال الآن"
            ann.dashboard_status_hint = "يظهر على الشاشة"
        elif ann.is_active and ann.starts_at and ann.starts_at > now:
            ann.dashboard_status_key = "future"
            ann.dashboard_status_label = "مجدول"
            ann.dashboard_status_hint = "سيظهر لاحقاً"
        elif not ann.is_active:
            ann.dashboard_status_key = "paused"
            ann.dashboard_status_label = "متوقف"
            ann.dashboard_status_hint = "لن يظهر على الشاشة"
        else:
            ann.dashboard_status_key = "expired"
            ann.dashboard_status_label = "منتهي"
            ann.dashboard_status_hint = "انتهت مدة عرضه"

    return render(
        request,
        "dashboard/ann_list.html",
        {
            "page": page,
            "active_count": active_count,
            "future_count": future_count,
            "expired_count": expired_count,
        },
    )


@manager_required
def ann_create(request):
    Announcement = AnnouncementModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    if request.method == "POST":
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            ann = form.save(commit=False)
            ann.school = school
            ann.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم إنشاء التنبيه.")
            return redirect("dashboard:ann_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = AnnouncementForm()
    return render(request, "dashboard/ann_form.html", {"form": form, "title": "إنشاء تنبيه"})


@manager_required
def ann_edit(request, pk: int):
    Announcement = AnnouncementModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    obj = get_object_or_404(Announcement, pk=pk, school=school)
    if request.method == "POST":
        form = AnnouncementForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم تحديث التنبيه.")
            return redirect("dashboard:ann_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = AnnouncementForm(instance=obj)
    return render(request, "dashboard/ann_form.html", {"form": form, "title": "تعديل تنبيه"})


@manager_required
@require_POST
def ann_delete(request, pk: int):
    Announcement = AnnouncementModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    obj = get_object_or_404(Announcement, pk=pk, school=school)
    obj.delete()
    _invalidate_display_cache(school)
    messages.success(request, "تم حذف التنبيه.")
    return redirect("dashboard:ann_list")


@manager_required
def exc_list(request):
    Excellence = ExcellenceModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    qs = Excellence.objects.filter(school=school).order_by("priority", "-start_at", "-id")

    now = timezone.now()
    active_count = Excellence.objects.filter(
        Q(school=school) & Q(start_at__lte=now) & (Q(end_at__isnull=True) | Q(end_at__gt=now))
    ).count()
    expired_count = Excellence.objects.filter(school=school, end_at__lte=now).count()
    max_p = Excellence.objects.filter(school=school).aggregate(m=Max("priority"))["m"] or 0

    page = Paginator(qs, 12).get_page(request.GET.get("page"))
    for item in page.object_list:
        if item.active_now:
            item.dashboard_status_key = "active"
            item.dashboard_status_label = "نشطة"
            item.dashboard_status_hint = "تظهر على الشاشة"
        elif item.start_at and item.start_at > now:
            item.dashboard_status_key = "future"
            item.dashboard_status_label = "مجدولة"
            item.dashboard_status_hint = "ستظهر لاحقاً"
        else:
            item.dashboard_status_key = "expired"
            item.dashboard_status_label = "منتهية"
            item.dashboard_status_hint = "انتهت مدة العرض"

    return render(
        request,
        "dashboard/exc_list.html",
        {"page": page, "active_count": active_count, "expired_count": expired_count, "max_priority": max_p},
    )


@manager_required
def exc_create(request):
    Excellence = ExcellenceModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    if request.method == "POST":
        form = ExcellenceForm(request.POST, request.FILES)
        if form.is_valid():
            exc = form.save(commit=False)
            exc.school = school
            exc.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم إضافة بطاقة التميز.")
            return redirect("dashboard:exc_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = ExcellenceForm()
    return render(request, "dashboard/exc_form.html", {"form": form, "title": "إضافة تميز"})


@manager_required
def exc_edit(request, pk: int):
    Excellence = ExcellenceModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    obj = get_object_or_404(Excellence, pk=pk, school=school)
    if request.method == "POST":
        form = ExcellenceForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            form.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم تحديث بطاقة التميز.")
            return redirect("dashboard:exc_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = ExcellenceForm(instance=obj)
    return render(request, "dashboard/exc_form.html", {"form": form, "title": "تعديل تميز"})


@manager_required
@require_POST
def exc_delete(request, pk: int):
    Excellence = ExcellenceModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    obj = get_object_or_404(Excellence, pk=pk, school=school)
    obj.delete()
    _invalidate_display_cache(school)
    messages.success(request, "تم حذف البطاقة.")
    return redirect("dashboard:exc_list")


# ======================
# حصص الانتظار
# ======================

@manager_required
def standby_list(request):
    StandbyAssignment = StandbyAssignmentModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    # فلتر التاريخ (مثل صفحة الإشراف والمناوبة)
    selected_date = timezone.localdate()
    date_str = request.GET.get("date", "").strip()
    if date_str:
        try:
            selected_date = timezone.datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            selected_date = timezone.localdate()

    # تصفية حسب التاريخ المحدد
    qs = StandbyAssignment.objects.filter(
        school=school,
        date=selected_date
    ).order_by("period_index", "-id")

    # إحصائيات
    total_count = qs.count()
    teachers_count = qs.values("teacher_name").distinct().count()

    page = Paginator(qs, 20).get_page(request.GET.get("page"))

    return render(
        request,
        "dashboard/standby_list.html",
        {
            "page": page,
            "selected_date": selected_date,
            "total_count": total_count,
            "teachers_count": teachers_count,
        },
    )


@manager_required
def standby_create(request):
    StandbyAssignment = StandbyAssignmentModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    initial = {}
    q_date = (request.GET.get("date") or "").strip()
    if q_date:
        try:
            initial["date"] = timezone.datetime.strptime(q_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    if request.method == "POST":
        form = StandbyForm(request.POST, school=school)
        if form.is_valid():
            standby = form.save(commit=False)
            standby.school = school
            standby.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم إضافة تكليف الانتظار.")
            return redirect("dashboard:standby_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = StandbyForm(initial=initial, school=school)

    return render(request, "dashboard/standby_form.html", {"form": form, "title": "إضافة تكليف"})


@manager_required
@require_POST
def standby_delete(request, pk: int):
    StandbyAssignment = StandbyAssignmentModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    obj = get_object_or_404(StandbyAssignment, pk=pk, school=school)
    obj.delete()
    _invalidate_display_cache(school)
    messages.success(request, "تم الحذف.")
    return redirect("dashboard:standby_list")





# ======================
# الإشراف والمناوبة
# ======================

@manager_required
def duty_list(request):
    DutyAssignment = DutyAssignmentModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    today = timezone.localdate()
    q_date = (request.GET.get("date") or "").strip()
    selected_date = today
    if q_date:
        try:
            selected_date = datetime.strptime(q_date, "%Y-%m-%d").date()
        except ValueError:
            messages.warning(request, "صيغة التاريخ غير صحيحة.")

    include_inactive = (request.GET.get("all") or "").strip() in {"1", "true", "yes"}

    qs = DutyAssignment.objects.filter(school=school, date=selected_date)
    if not include_inactive:
        qs = qs.filter(is_active=True)
    qs = qs.order_by("priority", "duty_type", "teacher_name", "-id")

    total_count = DutyAssignment.objects.filter(school=school, date=selected_date).count()
    active_count = DutyAssignment.objects.filter(school=school, date=selected_date, is_active=True).count()

    page = Paginator(qs, 25).get_page(request.GET.get("page"))

    return render(
        request,
        "dashboard/duty_list.html",
        {
            "page": page,
            "selected_date": selected_date,
            "include_inactive": include_inactive,
            "total_count": total_count,
            "active_count": active_count,
        },
    )


@manager_required
def duty_create(request):
    DutyAssignment = DutyAssignmentModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    initial = {}
    q_date = (request.GET.get("date") or "").strip()
    if q_date:
        try:
            initial["date"] = datetime.strptime(q_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    if request.method == "POST":
        form = DutyAssignmentForm(request.POST, school=school)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.school = school
            obj.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم إضافة تكليف الإشراف/المناوبة.")
            return redirect("dashboard:duty_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = DutyAssignmentForm(initial=initial, school=school)

    return render(request, "dashboard/duty_form.html", {"form": form, "title": "إضافة تكليف"})


@manager_required
def duty_edit(request, pk: int):
    DutyAssignment = DutyAssignmentModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    obj = get_object_or_404(DutyAssignment, pk=pk, school=school)

    if request.method == "POST":
        form = DutyAssignmentForm(request.POST, instance=obj, school=school)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.school = school
            updated.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم حفظ التعديلات.")
            return redirect("dashboard:duty_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = DutyAssignmentForm(instance=obj, school=school)

    return render(request, "dashboard/duty_form.html", {"form": form, "title": "تعديل تكليف"})


@manager_required
@require_POST
def duty_delete(request, pk: int):
    DutyAssignment = DutyAssignmentModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return response
    obj = get_object_or_404(DutyAssignment, pk=pk, school=school)
    obj.delete()
    _invalidate_display_cache(school)
    messages.success(request, "تم الحذف.")
    return redirect("dashboard:duty_list")


@manager_required
def duty_teacher_search(request):
    """JSON: اقتراح أسماء المعلمين للبحث الديناميكي داخل نموذج الإشراف/المناوبة."""
    Teacher = TeacherModel()
    school, response = get_active_school_or_redirect(request)
    if response:
        return JsonResponse({"results": [], "error": "no_active_school"}, status=403)

    q = (request.GET.get("q") or "").strip()
    # Reduce noise: don't hit DB for empty/1-char queries
    if len(q) < 2:
        return JsonResponse({"results": []})

    school_id = getattr(school, "id", None)
    cache_key = f"duty:teacher_search:{school_id}:{q.lower()}"
    try:
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            return JsonResponse({"results": cached})
    except Exception:
        pass

    qs = Teacher.objects.filter(school=school)
    if q:
        qs = qs.filter(name__icontains=q)

    names = list(qs.order_by("name").values_list("name", flat=True)[:10])
    try:
        cache.set(cache_key, names, timeout=60)
    except Exception:
        pass
    return JsonResponse({"results": names})


# ======================
# أدوات إضافية على الأيام
# ======================

@manager_required
@transaction.atomic
@require_POST
def day_clear(request, weekday: int):
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "اليوم خارج أيام الأسبوع الدراسية.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.warning(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day = get_object_or_404(DaySchedule, settings=settings_obj, weekday=weekday)

    periods_mgr = _rev_manager(day, "periods", "period_set")
    breaks_mgr = _rev_manager(day, "breaks", "break_set")

    if periods_mgr is not None:
        periods_mgr.all().delete()
    if breaks_mgr is not None:
        breaks_mgr.all().delete()

    messages.success(request, "تم مسح جميع الحصص والفسح لهذا اليوم.")
    return redirect("dashboard:day_edit", weekday=weekday)


@manager_required
@transaction.atomic
@require_POST
def day_reindex(request, weekday: int):
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "اليوم خارج أيام الأسبوع الدراسية.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.warning(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day = get_object_or_404(DaySchedule, settings=settings_obj, weekday=weekday)

    periods_mgr = _rev_manager(day, "periods", "period_set")
    if periods_mgr is None:
        messages.error(request, "تعذر الوصول للحصص (تحقق من related_name).")
        return redirect("dashboard:day_edit", weekday=weekday)

    periods = list(periods_mgr.all())
    periods.sort(key=lambda p: (p.starts_at or time.min, p.ends_at or time.min))

    for i, p in enumerate(periods, start=1):
        if p.index != i:
            p.index = i
            p.save(update_fields=["index"])

    messages.success(request, "تمت إعادة ترقيم الحصص حسب الترتيب الزمني (١..ن).")
    return redirect("dashboard:day_edit", weekday=weekday)


# ======================
# الحصص (ClassLesson)
# ======================

@manager_required
def lessons_list(request):
    SchoolSettings = SchoolSettingsModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    lessons = ClassLesson.objects.none()
    if settings_obj:
        lessons = (
            ClassLesson.objects.filter(settings=settings_obj)
            .select_related("school_class", "subject", "teacher")
            .order_by("weekday", "period_index", "school_class__name")
        )

    search = (request.GET.get("search") or "").strip()
    day = (request.GET.get("day") or "").strip()

    if search:
        lessons = lessons.filter(
            Q(school_class__name__icontains=search)
            | Q(subject__name__icontains=search)
            | Q(teacher__name__icontains=search)
        )

    if day.isdigit():
        lessons = lessons.filter(weekday=int(day))

    return render(request, "dashboard/lessons_list.html", {"lessons": lessons})


@manager_required
def lesson_create(request):
    SchoolSettings = SchoolSettingsModel()
    Subject = SubjectModel()
    Teacher = TeacherModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:lessons_list")

    form = LessonForm(request.POST or None)

    classes_qs = _classes_qs_from_settings(settings_obj).order_by("name")
    form.fields["school_class"].queryset = classes_qs
    form.fields["subject"].queryset = Subject.objects.filter(school=school).order_by("name")
    form.fields["teacher"].queryset = Teacher.objects.filter(school=school).order_by("name")

    if request.method == "POST":
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.settings = settings_obj
            lesson.save()
            _invalidate_display_cache(school)
            messages.success(request, "تمت إضافة الحصة بنجاح.")
            return redirect("dashboard:lessons_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")

    return render(request, "dashboard/lesson_form.html", {"form": form, "title": "إضافة حصة"})


@manager_required
def lesson_edit(request, pk: int):
    SchoolSettings = SchoolSettingsModel()
    ClassLesson = ClassLessonModel()
    Subject = SubjectModel()
    Teacher = TeacherModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:lessons_list")

    obj = get_object_or_404(ClassLesson, pk=pk, settings=settings_obj)

    form = LessonForm(request.POST or None, instance=obj)

    classes_qs = _classes_qs_from_settings(settings_obj).order_by("name")
    form.fields["school_class"].queryset = classes_qs
    form.fields["subject"].queryset = Subject.objects.filter(school=school).order_by("name")
    form.fields["teacher"].queryset = Teacher.objects.filter(school=school).order_by("name")

    if request.method == "POST":
        if form.is_valid():
            form.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم تعديل الحصة بنجاح.")
            return redirect("dashboard:lessons_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")

    return render(request, "dashboard/lesson_form.html", {"form": form, "title": "تعديل حصة"})


@manager_required
@require_POST
def lesson_delete(request, pk: int):
    SchoolSettings = SchoolSettingsModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:lessons_list")

    obj = get_object_or_404(ClassLesson, pk=pk, settings=settings_obj)
    obj.delete()
    _invalidate_display_cache(school)
    messages.success(request, "تم حذف الحصة.")
    return redirect("dashboard:lessons_list")


# ======================
# بيانات المدرسة (فصول/مواد/معلمين)
# ======================

@manager_required
def school_data(request):
    SchoolSettings = SchoolSettingsModel()
    Subject = SubjectModel()
    Teacher = TeacherModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()

    classes = _classes_qs_from_settings(settings_obj).order_by("name")
    subjects = Subject.objects.filter(school=school).order_by("name")
    teachers = Teacher.objects.filter(school=school).order_by("name")

    return render(
        request,
        "dashboard/school_data.html",
        {"classes": classes, "subjects": subjects, "teachers": teachers},
    )


@manager_required
@require_POST
def add_class(request):
    SchoolSettings = SchoolSettingsModel()
    SchoolClass = SchoolClassModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "فضلاً أدخل اسم الفصل.")
        return redirect("dashboard:school_data")

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    SchoolClass.objects.get_or_create(settings=settings_obj, name=name)
    messages.success(request, "تمت إضافة الفصل.")
    return redirect("dashboard:school_data")


@manager_required
@require_POST
def add_subject(request):
    Subject = SubjectModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "فضلاً أدخل اسم المادة.")
        return redirect("dashboard:school_data")

    Subject.objects.get_or_create(school=school, name=name)
    messages.success(request, "تمت إضافة المادة.")
    return redirect("dashboard:school_data")


@manager_required
@require_POST
def add_teacher(request):
    Teacher = TeacherModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "فضلاً أدخل اسم المعلم/ـة.")
        return redirect("dashboard:school_data")

    Teacher.objects.get_or_create(school=school, name=name)
    messages.success(request, "تمت إضافة المعلم/ـة.")
    return redirect("dashboard:school_data")


@manager_required
@require_POST
def delete_class(request, pk: int):
    SchoolClass = SchoolClassModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    deleted, _ = SchoolClass.objects.filter(pk=pk, settings__school=school).delete()
    if deleted:
        messages.success(request, "تم حذف الفصل.")
    else:
        messages.error(request, "الفصل غير موجود.")
    return redirect("dashboard:school_data")


@manager_required
@require_POST
def delete_subject(request, pk: int):
    Subject = SubjectModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    deleted, _ = Subject.objects.filter(pk=pk, school=school).delete()
    if deleted:
        messages.success(request, "تم حذف المادة.")
    else:
        messages.error(request, "المادة غير موجودة.")
    return redirect("dashboard:school_data")


@manager_required
@require_POST
def delete_teacher(request, pk: int):
    Teacher = TeacherModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    deleted, _ = Teacher.objects.filter(pk=pk, school=school).delete()
    if deleted:
        messages.success(request, "تم حذف المعلم/ـة.")
    else:
        messages.error(request, "المعلم/ـة غير موجود.")
    return redirect("dashboard:school_data")


# ======================
# جداول الحصص (يوم/أسبوع/تصدير)
# ======================

@manager_required
def timetable_day_view(request):
    SchoolSettings = SchoolSettingsModel()
    Period = PeriodModel()
    Subject = SubjectModel()
    Teacher = TeacherModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "يجب أولاً إعداد إعدادات المدرسة والجدول الزمني.")
        return redirect("dashboard:settings")

    weekdays_choices = list(SCHOOL_WEEK)
    default_weekday = weekdays_choices[0][0] if weekdays_choices else 0

    weekday_param = request.GET.get("weekday") if request.method == "GET" else request.POST.get("weekday")
    class_param = request.GET.get("class_id") if request.method == "GET" else request.POST.get("class_id")

    try:
        weekday = int(weekday_param) if weekday_param not in (None, "") else int(default_weekday)
    except (TypeError, ValueError):
        weekday = int(default_weekday)

    classes_qs = _classes_qs_from_settings(settings_obj).order_by("name")

    selected_class = None
    if classes_qs.exists():
        try:
            if class_param not in (None, ""):
                selected_class = classes_qs.get(pk=int(class_param))
            else:
                selected_class = classes_qs.first()
        except (ValueError, classes_qs.model.DoesNotExist):
            selected_class = classes_qs.first()

    selected_class_id = selected_class.id if selected_class else None

    periods_qs = Period.objects.filter(day__settings=settings_obj, day__weekday=weekday).order_by("index")
    subjects_qs = Subject.objects.filter(school=school).order_by("name")
    teachers_qs = Teacher.objects.filter(school=school).order_by("name")

    if selected_class is not None:
        existing_lessons_qs = ClassLesson.objects.filter(
            settings=settings_obj,
            weekday=weekday,
            school_class=selected_class,
        ).select_related("subject", "teacher")
    else:
        existing_lessons_qs = ClassLesson.objects.none()

    lessons_map: dict[int, object] = {l.period_index: l for l in existing_lessons_qs}

    if request.method == "POST" and selected_class is not None:
        created_count = 0
        updated_count = 0
        deleted_count = 0

        subjects_by_id = {s.id: s for s in subjects_qs}
        teachers_by_id = {t.id: t for t in teachers_qs}

        with transaction.atomic():
            for period in periods_qs:
                period_index = period.index
                existing_lesson = lessons_map.get(period_index)

                subject_field = f"subject-{selected_class.id}-{period_index}"
                teacher_field = f"teacher-{selected_class.id}-{period_index}"

                subject_raw = (request.POST.get(subject_field) or "").strip()
                teacher_raw = (request.POST.get(teacher_field) or "").strip()

                subject_obj = None
                teacher_obj = None

                if subject_raw:
                    try:
                        subject_obj = subjects_by_id.get(int(subject_raw))
                    except (TypeError, ValueError):
                        subject_obj = None

                if teacher_raw:
                    try:
                        teacher_obj = teachers_by_id.get(int(teacher_raw))
                    except (TypeError, ValueError):
                        teacher_obj = None

                if subject_obj is None and teacher_obj is None:
                    if existing_lesson is not None:
                        existing_lesson.delete()
                        deleted_count += 1
                    continue

                if existing_lesson is not None:
                    if subject_obj is None:
                        subject_obj = existing_lesson.subject
                    if teacher_obj is None:
                        teacher_obj = existing_lesson.teacher

                if subject_obj is None or teacher_obj is None:
                    continue

                if existing_lesson is None:
                    ClassLesson.objects.create(
                        settings=settings_obj,
                        school_class=selected_class,
                        weekday=weekday,
                        period_index=period_index,
                        subject=subject_obj,
                        teacher=teacher_obj,
                        is_active=True,
                    )
                    created_count += 1
                else:
                    changed = False
                    if existing_lesson.subject_id != subject_obj.id:
                        existing_lesson.subject = subject_obj
                        changed = True
                    if existing_lesson.teacher_id != teacher_obj.id:
                        existing_lesson.teacher = teacher_obj
                        changed = True
                    if not existing_lesson.is_active:
                        existing_lesson.is_active = True
                        changed = True
                    if changed:
                        existing_lesson.save()
                        updated_count += 1

        if created_count or updated_count or deleted_count:
            msg_parts = []
            if created_count:
                msg_parts.append(f"تم إنشاء {created_count} حصة جديدة.")
            if updated_count:
                msg_parts.append(f"تم تحديث {updated_count} حصة.")
            if deleted_count:
                msg_parts.append(f"تم حذف {deleted_count} حصة فارغة.")
            messages.success(request, " ".join(msg_parts))
        else:
            messages.info(request, "لم يتم رصد أي تغييرات في جدول هذا الفصل.")

        url = reverse("dashboard:timetable_day")
        url = f"{url}?weekday={weekday}&class_id={selected_class.id}"
        return redirect(url)

    rows: list[dict] = []
    for period in periods_qs:
        lesson = lessons_map.get(period.index)
        rows.append(
            {
                "period": period,
                "subject_id": lesson.subject_id if lesson else None,
                "teacher_id": lesson.teacher_id if lesson else None,
            }
        )

    return render(
        request,
        "dashboard/timetable_day.html",
        {
            "school": school,
            "settings": settings_obj,
            "weekdays": weekdays_choices,
            "weekday": weekday,
            "classes": classes_qs,
            "selected_class": selected_class,
            "selected_class_id": selected_class_id,
            "periods": periods_qs,
            "rows": rows,
            "subjects": subjects_qs,
            "teachers": teachers_qs,
        },
    )


@manager_required
def timetable_week_view(request):
    SchoolSettings = SchoolSettingsModel()
    Period = PeriodModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "يجب أولاً إعداد إعدادات المدرسة والجدول الزمني.")
        return redirect("dashboard:settings")

    class_param = request.GET.get("class_id")
    classes_qs = _classes_qs_from_settings(settings_obj).order_by("name")

    if not classes_qs.exists():
        messages.error(request, "لا توجد فصول مسجلة لهذه المدرسة.")
        return redirect("dashboard:timetable_day")

    try:
        selected_class = classes_qs.get(pk=int(class_param)) if class_param else classes_qs.first()
    except (ValueError, classes_qs.model.DoesNotExist):
        selected_class = classes_qs.first()

    all_periods_qs = Period.objects.filter(
        day__settings=settings_obj, day__weekday__in=SCHOOL_WEEKDAY_IDS
    ).order_by("day__weekday", "index")

    periods_by_weekday: dict[int, list] = {}
    for p in all_periods_qs:
        periods_by_weekday.setdefault(p.day.weekday, []).append(p)

    all_lessons_qs = ClassLesson.objects.filter(
        settings=settings_obj,
        school_class=selected_class,
        weekday__in=SCHOOL_WEEKDAY_IDS,
    ).select_related("subject", "teacher")

    lessons_by_weekday_period: dict[int, dict[int, object]] = {}
    for lesson in all_lessons_qs:
        lessons_by_weekday_period.setdefault(lesson.weekday, {})[lesson.period_index] = lesson

    days_data = []
    for weekday, label in SCHOOL_WEEK:
        rows = []
        for period in periods_by_weekday.get(weekday, []):
            lesson = lessons_by_weekday_period.get(weekday, {}).get(period.index)
            rows.append(
                {
                    "period": period,
                    "lesson": lesson,
                    "subject_name": lesson.subject.name if lesson and lesson.subject else "",
                    "teacher_name": lesson.teacher.name if lesson and lesson.teacher else "",
                }
            )
        days_data.append({"weekday": weekday, "label": label, "rows": rows})

    return render(
        request,
        "dashboard/timetable_week.html",
        {"school": school, "settings": settings_obj, "selected_class": selected_class, "days_data": days_data},
    )


@manager_required
def timetable_export_csv(request):
    SchoolSettings = SchoolSettingsModel()
    Period = PeriodModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "يجب أولاً إعداد إعدادات المدرسة والجدول الزمني.")
        return redirect("dashboard:settings")

    class_param = request.GET.get("class_id")
    if not class_param:
        messages.error(request, "لم يتم تحديد الفصل المطلوب للتصدير.")
        return redirect("dashboard:timetable_day")

    classes_qs = _classes_qs_from_settings(settings_obj)
    try:
        school_class = classes_qs.get(pk=int(class_param))
    except (ValueError, classes_qs.model.DoesNotExist):
        messages.error(request, "الفصل المحدد غير موجود.")
        return redirect("dashboard:timetable_day")

    periods_qs = Period.objects.filter(day__settings=settings_obj).select_related("day")
    period_map: dict[tuple[int, int], object] = {(p.day.weekday, p.index): p for p in periods_qs}

    lessons = (
        ClassLesson.objects.filter(settings=settings_obj, school_class=school_class)
        .select_related("subject", "teacher")
        .order_by("weekday", "period_index")
    )

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"timetable_{school_class.name}.csv"
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write("\ufeff")

    writer = csv.writer(resp)
    writer.writerow(["اليوم", "رقم الحصة", "وقت البداية", "وقت النهاية", "المادة", "المعلم/ـة"])

    for lesson in lessons:
        period = period_map.get((lesson.weekday, lesson.period_index))
        day_label = WEEKDAY_MAP.get(lesson.weekday, str(lesson.weekday))
        start_str = period.starts_at.strftime("%H:%M") if period and period.starts_at else ""
        end_str = period.ends_at.strftime("%H:%M") if period and period.ends_at else ""
        subject_name = lesson.subject.name if lesson.subject else ""
        teacher_name = lesson.teacher.name if lesson.teacher else ""
        writer.writerow([day_label, lesson.period_index, start_str, end_str, subject_name, teacher_name])

    return resp


# =========================
#  لوحة إدارة النظام (SaaS)
# =========================

def _admin_school_form_class():
    School = SchoolModel()

    class AdminSchoolForm(forms.ModelForm):
        class Meta:
            model = School

            fields = ["name", "slug", "school_type", "is_active"]

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            if "school_type" in self.fields:
                self.fields["school_type"].required = True
                # خيار توضيحي بدل "---------"
                choices = list(self.fields["school_type"].choices)
                if choices and choices[0][0] in ("", None):
                    choices[0] = ("", "اختر نوع المدرسة")
                else:
                    choices = [("", "اختر نوع المدرسة")] + choices
                self.fields["school_type"].choices = choices

    return AdminSchoolForm


def _admin_model_field_name(model, *field_names: str) -> str | None:
    """Return the first existing concrete field name from a compatibility list."""
    for field_name in field_names:
        try:
            model._meta.get_field(field_name)
            return field_name
        except Exception:
            continue
    return None


def _admin_order_by_existing(model, queryset, *candidates: str):
    fields: list[str] = []
    for candidate in candidates:
        field_name = candidate[1:] if candidate.startswith("-") else candidate
        if _admin_model_field_name(model, field_name):
            fields.append(candidate)
    if fields:
        return queryset.order_by(*fields)
    return queryset.order_by("-id")


@login_required
def switch_school(request, school_id=None):
    profile = _get_or_create_profile(request.user)
    School = SchoolModel()
    selected_school_id = school_id or request.POST.get("school_id") or request.GET.get("school_id")

    try:
        selected_school_id = int(selected_school_id)
    except (TypeError, ValueError):
        messages.error(request, "اختر مدرسة صحيحة للتبديل إليها.")
        return redirect(_safe_next_url(request, default_name="dashboard:index"))

    try:
        if getattr(request.user, "is_superuser", False):
            school = School.objects.get(pk=selected_school_id)
            if not profile.schools.filter(pk=school.pk).exists():
                profile.schools.add(school)
        else:
            school = profile.schools.get(pk=selected_school_id)
    except School.DoesNotExist:
        messages.error(request, "المدرسة غير موجودة أو ليس لديك صلاحية الوصول إليها.")
        return redirect("dashboard:index")
    except Exception:
        messages.error(request, "المدرسة غير موجودة أو ليس لديك صلاحية الوصول إليها.")
        return redirect("dashboard:index")

    profile.active_school = school
    profile.save(update_fields=["active_school"])
    request.school = school
    request.session.pop("sub_blocked_once", None)
    messages.success(request, f"تم التبديل إلى مدرسة: {school.name}")
    return redirect(_safe_next_url(request, default_name="dashboard:index"))


@system_staff_required
def system_admin_dashboard(request):
    School = SchoolModel()

    school_count = School.objects.count()
    user_count = UserModel.objects.count()

    SubModel = _get_subscription_model()
    subs_count = SubModel.objects.count() if SubModel is not None else 0

    today = timezone.localdate()
    active_subs = 0
    revenue = 0

    if SubModel is not None:
        active_qs = SubModel.objects.filter(status="active").filter(
            Q(ends_at__isnull=True) | Q(ends_at__gte=today)
        )
        active_subs = active_qs.count()
        revenue = active_qs.aggregate(total=Sum("plan__price"))["total"] or 0

    Req = _get_subscription_request_model()
    open_requests = 0
    if Req is not None:
        try:
            open_requests = Req.objects.filter(status__in=["submitted", "under_review"]).count()
        except Exception:
            open_requests = 0

    return render(
        request,
        "admin/dashboard.html",
        {
            "schools_count": school_count,
            "users_count": user_count,
            "subs_count": subs_count,
            "active_subs": active_subs,
            "subscriptions_count": active_subs,
            "revenue": revenue,
            "open_subscription_requests": open_requests,
        },
    )


@system_staff_required
def system_schools_list(request):
    School = SchoolModel()
    DisplayScreen = DisplayScreenModel()

    q = (request.GET.get("q") or "").strip()
    schools = School.objects.all()

    if q:
        schools = schools.filter(Q(name__icontains=q) | Q(slug__icontains=q))

    schools = _admin_order_by_existing(School, schools, "-created_at", "-id")

    # attach live stats
    try:
        schools = schools.select_related("schedule_settings")
    except Exception:
        pass

    active_window_seconds = 120
    active_since = timezone.now() - timedelta(seconds=active_window_seconds)
    screens_qs = DisplayScreen.objects.all()

    if _model_has_field(DisplayScreen, "is_active"):
        screens_qs = screens_qs.filter(is_active=True)
    last_seen_field = _admin_model_field_name(DisplayScreen, "last_seen_at", "last_seen")
    if last_seen_field:
        screens_qs = screens_qs.filter(**{f"{last_seen_field}__gte": active_since})
    if _model_has_field(DisplayScreen, "bound_device_id"):
        screens_qs = screens_qs.exclude(bound_device_id__isnull=True).exclude(bound_device_id="")

    active_counts = {
        row["school_id"]: row["c"]
        for row in screens_qs.values("school_id").annotate(c=Count("id"))
        if row.get("school_id")
    }

    paginator = Paginator(schools, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    for s in page_obj.object_list:
        setattr(s, "active_screens_now", int(active_counts.get(s.id, 0) or 0))
        settings_obj = getattr(s, "schedule_settings", None)
        setattr(s, "refresh_interval_sec", getattr(settings_obj, "refresh_interval_sec", None))

    return render(
        request,
        "admin/schools_list.html",
        {
            "schools": page_obj.object_list,
            "page_obj": page_obj,
            "q": q,
            "active_window_seconds": active_window_seconds,
        },
    )


@system_staff_required
def system_school_create(request):
    FormCls = _admin_school_form_class()

    if request.method == "POST":
        form = FormCls(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, "تمت إضافة المدرسة بنجاح.")
            return redirect("dashboard:system_schools_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = FormCls()

    return render(request, "admin/school_form.html", {"form": form, "title": "إضافة مدرسة"})


@system_staff_required
def system_school_edit(request, pk: int):
    School = SchoolModel()
    FormCls = _admin_school_form_class()

    school = get_object_or_404(School, pk=pk)
    if request.method == "POST":
        form = FormCls(request.POST, request.FILES, instance=school)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث بيانات المدرسة.")
            return redirect("dashboard:system_schools_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = FormCls(instance=school)

    return render(request, "admin/school_form.html", {"form": form, "title": "تعديل مدرسة", "edit": True})


@superuser_required
def system_school_delete(request, pk: int):
    School = SchoolModel()
    school = get_object_or_404(School, pk=pk)
    if request.method == "POST":
        school.delete()
        messages.warning(request, f"تم حذف المدرسة: {school.name}")
        return redirect("dashboard:system_schools_list")
    return render(request, "admin/school_confirm_delete.html", {"school": school})


@system_staff_required
def system_users_list(request):
    q = (request.GET.get("q") or "").strip()

    qs = UserModel.objects.all().order_by("-id")

    try:
        qs = qs.select_related("profile", "profile__active_school").prefetch_related("profile__schools")
    except FieldError:
        try:
            qs = qs.select_related("profile")
        except FieldError:
            pass

    if q:
        filters = Q()
        for key in ("username", "email", "first_name", "last_name"):
            try:
                UserModel._meta.get_field(key)
                filters |= Q(**{f"{key}__icontains": q})
            except Exception:
                continue

        try:
            filters |= Q(profile__active_school__name__icontains=q) | Q(profile__schools__name__icontains=q)
        except Exception:
            pass

        qs = qs.filter(filters).distinct()

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "admin/users_list.html", {"page_obj": page_obj, "q": q})


@system_staff_required
def system_employees_list(request):
    """قائمة موظفي النظام (Superuser / Support group)."""
    q = (request.GET.get("q") or "").strip()

    qs = (
        UserModel.objects.filter(
            Q(is_staff=True) | Q(is_superuser=True) | Q(groups__name="Support")
        )
        .distinct()
        .order_by("-id")
    )

    try:
        qs = qs.prefetch_related("groups")
    except Exception:
        pass

    if q:
        filters = Q()
        for key in ("username", "email", "first_name", "last_name"):
            try:
                UserModel._meta.get_field(key)
                filters |= Q(**{f"{key}__icontains": q})
            except Exception:
                continue
        filters |= Q(groups__name__icontains=q)
        qs = qs.filter(filters).distinct()

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "admin/employees_list.html", {"page_obj": page_obj, "q": q})


@system_staff_required
def system_employee_create(request):
    if request.method == "POST":
        form = SystemEmployeeCreateForm(request.POST)
        # منع غير السوبر من إنشاء superuser
        if not getattr(request.user, "is_superuser", False):
            form.fields["role"].choices = [
                (SystemEmployeeCreateForm.ROLE_SUPPORT, "موظف دعم"),
            ]
        else:
            form.fields["role"].choices = [
                (SystemEmployeeCreateForm.ROLE_SUPPORT, "موظف دعم"),
                (SystemEmployeeCreateForm.ROLE_SUPERUSER, "مدير نظام (superuser)"),
            ]

        if form.is_valid():
            role = form.cleaned_data.get("role")
            if role == SystemEmployeeCreateForm.ROLE_SUPERUSER and not getattr(request.user, "is_superuser", False):
                raise PermissionDenied("لا تملك صلاحية إنشاء مدير نظام.")

            form.save()
            messages.success(request, "تم إنشاء الموظف بنجاح.")
            return redirect("dashboard:system_employees_list")

        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = SystemEmployeeCreateForm()
        if getattr(request.user, "is_superuser", False):
            form.fields["role"].choices = [
                (SystemEmployeeCreateForm.ROLE_SUPPORT, "موظف دعم"),
                (SystemEmployeeCreateForm.ROLE_SUPERUSER, "مدير نظام (superuser)"),
            ]

    return render(request, "admin/employee_form.html", {"form": form})


@system_staff_required
def system_user_create(request):
    if request.method == "POST":
        form = SystemUserCreateForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إنشاء المستخدم بنجاح.")
            return redirect("dashboard:system_users_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = SystemUserCreateForm()

    # موظف الدعم لا يُسمح له بترقية المستخدمين إلى staff/superuser
    if not getattr(request.user, "is_superuser", False):
        for f in ("is_staff", "is_superuser"):
            if f in form.fields:
                del form.fields[f]

    return render(request, "admin/user_edit.html", {"form": form, "is_create": True})


@system_staff_required
def system_user_edit(request, pk: int):
    user = get_object_or_404(UserModel, pk=pk)

    # موظف الدعم لا يعدّل حسابات السوبر
    if not getattr(request.user, "is_superuser", False) and getattr(user, "is_superuser", False):
        raise PermissionDenied("لا تملك صلاحية تعديل هذا المستخدم.")

    if request.method == "POST":
        form = SystemUserUpdateForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث بيانات المستخدم بنجاح.")
            return redirect("dashboard:system_users_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = SystemUserUpdateForm(instance=user)

    # موظف الدعم لا يُسمح له بتعديل staff/superuser
    if not getattr(request.user, "is_superuser", False):
        for f in ("is_staff", "is_superuser"):
            if f in form.fields:
                del form.fields[f]

    return render(request, "admin/user_edit.html", {"form": form, "is_create": False, "user_obj": user})


@superuser_required
def system_user_delete(request, pk: int):
    user = get_object_or_404(UserModel, pk=pk)

    if request.method == "POST":
        username = getattr(user, "username", str(user.pk))
        user.delete()
        messages.success(request, f"تم حذف المستخدم {username}.")
        return redirect("dashboard:system_users_list")

    return render(request, "admin/user_delete_confirm.html", {"user_obj": user})


# =====================
# 💳 إدارة الاشتراكات
# =====================

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
                "No subscription model available (subscriptions/core). Subscription admin views may be degraded."
            )
            return None

def _get_subscription_model():
    return _get_subscription_model_robust()

@system_staff_required
def system_subscriptions_list(request):
    SubModel = _get_subscription_model_robust()

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()  # active | inactive | ""

    today = timezone.localdate()

    rows = []
    page_obj = None

    active_count = 0
    inactive_count = 0
    total_count = 0

    if SubModel is None:
        return render(
            request,
            "admin/subscriptions_list.html",
            {
                "rows": rows,
                "page_obj": page_obj,
                "q": q,
                "status": status,
                "active_count": 0,
                "inactive_count": 0,
                "total_count": 0,
            },
        )

    # ---------- بناء QuerySet مرن حسب الحقول ----------
    qs = SubModel.objects.all()

    # روابط شائعة
    has_school = True
    has_plan = True
    try:
        SubModel._meta.get_field("school")
    except Exception:
        has_school = False
    try:
        SubModel._meta.get_field("plan")
    except Exception:
        has_plan = False

    if has_school:
        try:
            qs = qs.select_related("school")
        except Exception:
            pass
    if has_plan:
        try:
            qs = qs.select_related("plan")
        except Exception:
            pass

    # فلترة البحث
    if q:
        filters = Q()
        if has_school:
            filters |= Q(school__name__icontains=q)
            # email في School غير موجود عندك في core/models.py → لن نفلتر عليه
        if has_plan:
            filters |= Q(plan__name__icontains=q)
        qs = qs.filter(filters).distinct()

    # ---------- توحيد منطق "نشط" بين النظامين ----------
    # New subscriptions عادة: starts_at/ends_at + status
    # Legacy core: start_date/end_date + is_active
    def _is_active_obj(sub) -> bool:
        # boolean مباشر لو موجود
        if hasattr(sub, "is_active"):
            try:
                if not bool(sub.is_active):
                    return False
            except Exception:
                pass

        # status لو موجود
        if hasattr(sub, "status"):
            try:
                if str(sub.status) != "active":
                    return False
            except Exception:
                pass

        # ends_at / end_date
        end_val = getattr(sub, "ends_at", None)
        if end_val is None:
            end_val = getattr(sub, "end_date", None)

        if end_val:
            try:
                return end_val >= today
            except Exception:
                return True

        return True

    def _starts_at(sub):
        v = getattr(sub, "starts_at", None)
        if v is None:
            v = getattr(sub, "start_date", None)
        return v

    def _ends_at(sub):
        v = getattr(sub, "ends_at", None)
        if v is None:
            v = getattr(sub, "end_date", None)
        return v

    # ترتيب
    # new: -starts_at, -id | legacy: -start_date, -id
    qs = _admin_order_by_existing(SubModel, qs, "-starts_at", "-start_date", "-id")

    # نحسب الحالة على كامل النتائج قبل الترقيم حتى لا تظهر صفحات فارغة بعد الفلترة.
    try:
        all_subscriptions = list(qs)
    except Exception:
        all_subscriptions = []

    total_count = len(all_subscriptions)
    active_count = sum(1 for sub in all_subscriptions if _is_active_obj(sub))
    inactive_count = total_count - active_count

    if status == "active":
        display_subscriptions = [sub for sub in all_subscriptions if _is_active_obj(sub)]
    elif status == "inactive":
        display_subscriptions = [sub for sub in all_subscriptions if not _is_active_obj(sub)]
    else:
        display_subscriptions = all_subscriptions

    paginator = Paginator(display_subscriptions, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    # بناء rows جاهزة للقالب
    filtered_rows = []
    for sub in page_obj.object_list:
        is_active = _is_active_obj(sub)

        school_obj = getattr(sub, "school", None)
        plan_obj = getattr(sub, "plan", None)

        filtered_rows.append(
            {
                "id": sub.pk,
                "school_name": getattr(school_obj, "name", "") if school_obj else "—",
                "school_email": getattr(school_obj, "email", "") if school_obj else "",
                "plan_name": getattr(plan_obj, "name", "") if plan_obj else "—",
                "starts_at": _starts_at(sub),
                "ends_at": _ends_at(sub),
                "is_active": is_active,
            }
        )

    return render(
        request,
        "admin/subscriptions_list.html",
        {
            "rows": filtered_rows,
            "page_obj": page_obj,
            "q": q,
            "status": status,
            "active_count": active_count,
            "inactive_count": inactive_count,
            "total_count": total_count,
        },
    )


@system_staff_required
def system_subscription_create(request):
    SubModel = _get_subscription_model()
    if SubModel is None:
        messages.error(request, "نظام الاشتراكات غير مثبت.")
        return redirect("dashboard:system_subscriptions_list")

    plan_durations = {p.id: p.duration_days for p in SubscriptionPlan.objects.all().only("id", "duration_days")}
    plan_prices = {p.id: str(p.price) for p in SubscriptionPlan.objects.all().only("id", "price")}

    if request.method == "POST":
        form = SchoolSubscriptionForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            # احسب تاريخ النهاية تلقائيًا من مدة الخطة إذا كانت النهاية غير محددة.
            try:
                if not getattr(obj, "ends_at", None) and getattr(obj, "starts_at", None) and getattr(obj, "plan", None):
                    days = getattr(obj.plan, "duration_days", None)
                    if days is not None:
                        days_int = int(days)
                        if days_int > 0:
                            obj.ends_at = obj.starts_at + timedelta(days=days_int)
            except Exception:
                pass
            obj.save()

            # سجل طريقة الدفع عند إنشاء اشتراك مدفوع يدويًا (لا تشمل الباقة المجانية)
            try:
                from subscriptions.models import SubscriptionPaymentOperation

                plan_obj = getattr(obj, "plan", None)
                price = getattr(plan_obj, "price", 0) if plan_obj is not None else 0
                method = (form.cleaned_data.get("payment_method") or "").strip()

                if plan_obj is not None and float(price or 0) > 0:
                    if method:
                        SubscriptionPaymentOperation.objects.create(
                            school=getattr(obj, "school", None),
                            subscription=obj,
                            plan=plan_obj,
                            amount=price or 0,
                            method=method,
                            source="admin_manual",
                            created_by=request.user,
                        )
            except Exception:
                pass

            messages.success(request, "تم إنشاء الاشتراك بنجاح.")
            return redirect("dashboard:system_subscriptions_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = SchoolSubscriptionForm()

    return render(
        request,
        "admin/subscription_form.html",
        {"form": form, "title": "إضافة اشتراك", "plan_durations": plan_durations, "plan_prices": plan_prices},
    )


@system_staff_required
def system_subscription_edit(request, pk: int):
    SubModel = _get_subscription_model()
    if SubModel is None:
        messages.error(request, "نظام الاشتراكات غير مثبت.")
        return redirect("dashboard:system_subscriptions_list")

    plan_durations = {p.id: p.duration_days for p in SubscriptionPlan.objects.all().only("id", "duration_days")}
    plan_prices = {p.id: str(p.price) for p in SubscriptionPlan.objects.all().only("id", "price")}

    obj = get_object_or_404(SubModel, pk=pk)

    if request.method == "POST":
        form = SchoolSubscriptionForm(request.POST, instance=obj)
        if form.is_valid():
            obj2 = form.save(commit=False)
            # احسب تاريخ النهاية تلقائيًا من مدة الخطة إذا كانت النهاية غير محددة.
            try:
                if not getattr(obj2, "ends_at", None) and getattr(obj2, "starts_at", None) and getattr(obj2, "plan", None):
                    days = getattr(obj2.plan, "duration_days", None)
                    if days is not None:
                        days_int = int(days)
                        if days_int > 0:
                            obj2.ends_at = obj2.starts_at + timedelta(days=days_int)
            except Exception:
                pass
            obj2.save()

            payment_method_saved_label = None
            payment_method_save_failed = False

            # حفظ/تحديث طريقة الدفع لاشتراك سابق (عملية دفع + فاتورة إن وجدت)
            try:
                from subscriptions.models import SubscriptionPaymentOperation

                plan_obj = getattr(obj2, "plan", None)
                price = getattr(plan_obj, "price", 0) if plan_obj is not None else 0
                method = (form.cleaned_data.get("payment_method") or "").strip()

                if plan_obj is not None and float(price or 0) > 0 and method:
                    # استخدم آخر عملية دفع موجودة للاشتراك (سواء كانت من طلب أو إضافة يدوية)
                    op = (
                        SubscriptionPaymentOperation.objects.filter(
                            school=getattr(obj2, "school", None),
                            subscription=obj2,
                        )
                        .order_by("-created_at", "-id")
                        .first()
                    )

                    if op is None:
                        op = SubscriptionPaymentOperation.objects.create(
                            school=getattr(obj2, "school", None),
                            subscription=obj2,
                            plan=plan_obj,
                            amount=price or 0,
                            method=method,
                            source="admin_manual",
                            created_by=request.user,
                            note="Updated via edit",
                        )
                    else:
                        changed = False
                        if getattr(op, "method", None) != method:
                            op.method = method
                            changed = True
                        if getattr(op, "plan_id", None) != getattr(plan_obj, "id", None):
                            op.plan = plan_obj
                            changed = True
                        try:
                            if float(getattr(op, "amount", 0) or 0) != float(price or 0):
                                op.amount = price or 0
                                changed = True
                        except Exception:
                            pass
                        if changed:
                            op.save(update_fields=["method", "plan", "amount"])

                    try:
                        payment_method_saved_label = getattr(op, "get_method_display", lambda: op.method)()
                    except Exception:
                        payment_method_saved_label = op.method

                    # تحديث/إنشاء الفاتورة بشكل منفصل حتى لا يمنع حفظ طريقة الدفع
                    try:
                        from django.template.loader import render_to_string

                        from subscriptions.invoicing import _get_seller_info, _get_school_contact_info, build_invoice_from_operation

                        try:
                            inv = getattr(op, "invoice", None)
                        except Exception:
                            inv = None

                        if inv is None:
                            build_invoice_from_operation(op)
                        else:
                            inv.payment_method = op.method
                            inv.amount = op.amount
                            inv.plan = op.plan

                            c_name, c_mobile = _get_school_contact_info(inv.school)

                            html = render_to_string(
                                "invoices/subscription_invoice.html",
                                {
                                    "invoice": inv,
                                    "seller": _get_seller_info(),
                                    "school": inv.school,
                                    "subscription": inv.subscription,
                                    "plan": inv.plan,
                                    "contact_name": c_name,
                                    "contact_mobile": c_mobile,
                                },
                            )
                            inv.html_snapshot = html
                            inv.save(update_fields=["payment_method", "amount", "plan", "html_snapshot"])
                    except Exception:
                        logger.exception("Failed to update/generate invoice for subscription %s", getattr(obj2, "pk", None))
            except Exception:
                logger.exception("Failed to update payment method for subscription %s", getattr(obj2, "pk", None))
                payment_method_save_failed = True

            if payment_method_save_failed:
                messages.warning(request, "تم حفظ الاشتراك، لكن تعذّر حفظ/تحديث طريقة الدفع. راجع سجل الأخطاء.")
            elif payment_method_saved_label:
                messages.success(request, f"تم حفظ طريقة الدفع: {payment_method_saved_label}.")

            messages.success(request, "تم تحديث بيانات الاشتراك.")
            return redirect("dashboard:system_subscriptions_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = SchoolSubscriptionForm(instance=obj)

    return render(
        request,
        "admin/subscription_form.html",
        {"form": form, "title": "تعديل اشتراك", "edit": True, "plan_durations": plan_durations, "plan_prices": plan_prices},
    )


@superuser_required
@require_POST
def system_subscription_delete(request, pk: int):
    SubModel = _get_subscription_model()
    if SubModel is None:
        messages.error(request, "نظام الاشتراكات غير مثبت.")
        return redirect("dashboard:system_subscriptions_list")

    obj = get_object_or_404(SubModel, pk=pk)
    obj.delete()
    messages.warning(request, "تم حذف الاشتراك.")
    return redirect("dashboard:system_subscriptions_list")


@system_staff_required
def system_subscription_invoice_view(request, pk: int):
    """عرض الفاتورة (HTML snapshot) لعمليات الدفع."""
    from django.http import HttpResponse

    from subscriptions.models import SubscriptionInvoice

    invoice = get_object_or_404(SubscriptionInvoice.objects.select_related("school", "subscription", "plan"), pk=pk)
    html = (invoice.html_snapshot or "").strip()

    # كحل احتياطي: إن لم تُحفظ النسخة لأي سبب، نعيد توليدها.
    if not html:
        try:
            from django.template.loader import render_to_string

            from subscriptions.invoicing import _get_seller_info

            html = render_to_string(
                "invoices/subscription_invoice.html",
                {
                    "invoice": invoice,
                    "seller": _get_seller_info(),
                    "school": invoice.school,
                    "subscription": invoice.subscription,
                    "plan": invoice.plan,
                },
            )
            invoice.html_snapshot = html
            invoice.save(update_fields=["html_snapshot"])
        except Exception:
            html = ""

    if not html:
        messages.error(request, "تعذر عرض الفاتورة حاليًا.")
        return redirect("dashboard:system_subscriptions_list")

    return HttpResponse(html, content_type="text/html; charset=utf-8")


# ===============================
# 🧾 طلبات التجديد/الاشتراك (Admin)
# ===============================


@system_staff_required
def system_subscription_requests_list(request):
    Req = _get_subscription_request_model()
    if Req is None:
        messages.error(request, "نظام طلبات الاشتراك غير مثبت.")
        return redirect("dashboard:system_admin_dashboard")

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    req_type = (request.GET.get("type") or "").strip()

    try:
        qs = Req.objects.all()
        try:
            qs = qs.select_related("school", "plan", "created_by", "processed_by")
        except Exception:
            pass

        if q:
            qs = qs.filter(
                Q(school__name__icontains=q)
                | Q(school__slug__icontains=q)
                | Q(plan__name__icontains=q)
            )

        counts_qs = qs

        if req_type:
            counts_qs = counts_qs.filter(request_type=req_type)

        open_count = counts_qs.filter(status__in=["submitted", "under_review"]).count()
        approved_count = counts_qs.filter(status="approved").count()
        rejected_count = counts_qs.filter(status="rejected").count()

        if status:
            qs = qs.filter(status=status)
        if req_type:
            qs = qs.filter(request_type=req_type)

        qs = qs.order_by("-created_at", "-id")

        paginator = Paginator(qs, 25)
        page_obj = paginator.get_page(request.GET.get("page") or 1)
    except (ProgrammingError, OperationalError):
        # غالباً: لم يتم تشغيل migrate في بيئة الإنتاج بعد نشر الكود.
        messages.error(request, "قاعدة البيانات غير محدثة (جدول طلبات الاشتراك غير موجود). نفّذ migrate ثم أعد المحاولة.")
        open_count = 0
        approved_count = 0
        rejected_count = 0
        page_obj = Paginator([], 25).get_page(1)

    return render(
        request,
        "admin/subscription_requests_list.html",
        {
            "page_obj": page_obj,
            "q": q,
            "status": status,
            "request_type": req_type,
            "open_count": open_count,
            "approved_count": approved_count,
            "rejected_count": rejected_count,
        },
    )


@system_staff_required
def system_subscription_request_detail(request, pk: int):
    Req = _get_subscription_request_model()
    if Req is None:
        messages.error(request, "نظام طلبات الاشتراك غير مثبت.")
        return redirect("dashboard:system_admin_dashboard")

    try:
        obj = get_object_or_404(Req, pk=pk)
    except (ProgrammingError, OperationalError):
        messages.error(request, "قاعدة البيانات غير محدثة (جدول طلبات الاشتراك غير موجود). نفّذ migrate ثم أعد المحاولة.")
        return redirect("dashboard:system_subscription_requests_list")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        admin_note = (request.POST.get("admin_note") or "").strip()

        if action not in {"approve", "reject", "under_review"}:
            messages.error(request, "إجراء غير صالح.")
            return redirect("dashboard:system_subscription_request_detail", pk=pk)

        if obj.status == "approved" and action == "approve":
            messages.info(request, "هذا الطلب معتمد بالفعل.")
            return redirect("dashboard:system_subscription_request_detail", pk=pk)

        if obj.status == "rejected" and action == "reject":
            messages.info(request, "هذا الطلب مرفوض بالفعل.")
            return redirect("dashboard:system_subscription_request_detail", pk=pk)

        if action == "under_review":
            if obj.status in {"approved", "rejected"}:
                messages.warning(request, "لا يمكن تغيير حالة طلب مُعالج.")
                return redirect("dashboard:system_subscription_request_detail", pk=pk)
            obj.status = "under_review"
            obj.admin_note = admin_note
            obj.processed_by = request.user
            obj.processed_at = timezone.now()
            obj.save(update_fields=["status", "admin_note", "processed_by", "processed_at", "updated_at"])
            messages.success(request, "تم تحويل الطلب إلى (قيد المراجعة).")
            return redirect("dashboard:system_subscription_request_detail", pk=pk)

        if action == "reject":
            if obj.status == "approved":
                messages.warning(request, "لا يمكن رفض طلب مُعتمد.")
                return redirect("dashboard:system_subscription_request_detail", pk=pk)
            obj.status = "rejected"
            obj.admin_note = admin_note
            obj.processed_by = request.user
            obj.processed_at = timezone.now()
            obj.save(update_fields=["status", "admin_note", "processed_by", "processed_at", "updated_at"])
            messages.success(request, "تم رفض الطلب.")
            return redirect("dashboard:system_subscription_request_detail", pk=pk)

        if action == "approve":
            if obj.status == "rejected":
                messages.warning(request, "لا يمكن اعتماد طلب مرفوض.")
                return redirect("dashboard:system_subscription_request_detail", pk=pk)

            SubNew = None
            try:
                SubNew = apps.get_model("subscriptions", "SchoolSubscription")
            except Exception:
                SubNew = None
            if SubNew is None:
                messages.error(request, "موديل الاشتراكات غير متوفر لإنشاء الاشتراك.")
                return redirect("dashboard:system_subscription_request_detail", pk=pk)

            with transaction.atomic():
                # منع التكرار وفق unique constraint
                sub_obj, _ = SubNew.objects.get_or_create(
                    school=obj.school,
                    plan=obj.plan,
                    starts_at=obj.requested_starts_at,
                    defaults={
                        "status": "active",
                        "notes": f"Approved from request #{obj.pk}",
                    },
                )

                obj.status = "approved"
                obj.admin_note = admin_note
                obj.processed_by = request.user
                obj.processed_at = timezone.now()
                obj.approved_subscription = sub_obj
                obj.save(
                    update_fields=[
                        "status",
                        "admin_note",
                        "processed_by",
                        "processed_at",
                        "approved_subscription",
                        "updated_at",
                    ]
                )

                # إنشاء عملية دفع لطلبات الاشتراك المدفوعة (لإنشاء الفاتورة تلقائياً عبر signals)
                try:
                    from subscriptions.models import SubscriptionPaymentOperation

                    amt = getattr(obj, "amount", 0) or 0
                    if float(amt) > 0 and not SubscriptionPaymentOperation.objects.filter(
                        school=obj.school,
                        subscription=sub_obj,
                        source="request",
                    ).exists():
                        SubscriptionPaymentOperation.objects.create(
                            school=obj.school,
                            subscription=sub_obj,
                            plan=obj.plan,
                            amount=amt,
                            method="bank_transfer",
                            source="request",
                            created_by=request.user,
                            note=f"Approved from request #{obj.pk}",
                        )
                except Exception:
                    pass

            messages.success(request, "تم اعتماد الطلب وإنشاء/ربط الاشتراك.")
            return redirect("dashboard:system_subscription_request_detail", pk=pk)

    return render(request, "admin/subscription_request_detail.html", {"obj": obj})


# ==========================
# ✅ اشتراكي (مدرسة المستخدم)
# ==========================

@login_required
def my_subscription(request):
    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    SubModel = _get_subscription_model()
    today = timezone.localdate()

    current_subscription = None
    upcoming_subscription = None
    primary_subscription = None
    primary_label = ""

    current_status_code = "none"
    current_status_label = "لا يوجد اشتراك"
    current_status_badge_class = "bg-rose-50 text-rose-700"

    DisplayScreen = DisplayScreenModel()
    screens = list(DisplayScreen.objects.filter(school=school))
    screens_total_count = len(screens)
    active_screens = [screen for screen in screens if bool(getattr(screen, "is_active", False))]
    screens_active_count = len(active_screens)
    screens_live_count = live_display_count(active_screens)
    screens_never_connected_count = sum(
        1
        for screen in active_screens
        if latest_display_presence(screen) is None
        and not (getattr(screen, "bound_device_id", "") or "").strip()
    )

    if screens_total_count == 0:
        screen_status_label = "لا توجد شاشات مسجلة"
        screen_status_class = "text-slate-600"
    elif screens_active_count == 0:
        screen_status_label = "جميع الشاشات معطلة"
        screen_status_class = "text-rose-600"
    elif screens_live_count == screens_active_count:
        screen_status_label = f"كل الشاشات متصلة الآن ({screens_live_count}/{screens_active_count})"
        screen_status_class = "text-emerald-700"
    elif screens_live_count > 0:
        screen_status_label = f"{screens_live_count} من {screens_active_count} شاشة متصلة الآن"
        screen_status_class = "text-amber-700"
    elif screens_never_connected_count == screens_active_count:
        screen_status_label = f"بانتظار أول اتصال ({screens_active_count} شاشة)"
        screen_status_class = "text-amber-700"
    else:
        screen_status_label = f"لا توجد شاشة متصلة حاليًا (0/{screens_active_count})"
        screen_status_class = "text-rose-600"

    def _field_exists(model_cls, field_name: str) -> bool:
        try:
            model_cls._meta.get_field(field_name)
            return True
        except Exception:
            return False

    def _get_attr(obj, name: str, default=None):
        try:
            return getattr(obj, name)
        except Exception:
            return default

    def _compute_display_ends_at(sub, start_field: str | None, end_field: str | None):
        if sub is None:
            return None
        end_value = _get_attr(sub, end_field) if end_field else None
        if end_value:
            return end_value

        plan = _get_attr(sub, "plan")
        start_value = _get_attr(sub, start_field) if start_field else None
        if plan is not None and start_value:
            try:
                days = _get_attr(plan, "duration_days")
                if days is not None:
                    days_int = int(days)
                    if days_int > 0:
                        return start_value + timedelta(days=days_int)
            except Exception:
                pass

        return None

    def _attach_display_fields(sub, start_field: str | None, end_field: str | None):
        if sub is None:
            return
        display_starts_at = _get_attr(sub, start_field) if start_field else None
        display_ends_at = _compute_display_ends_at(sub, start_field, end_field)
        try:
            setattr(sub, "display_starts_at", display_starts_at)
            setattr(sub, "display_ends_at", display_ends_at)
            if display_ends_at:
                setattr(sub, "display_days_left", max(0, int((display_ends_at - today).days)))
            else:
                setattr(sub, "display_days_left", None)
        except Exception:
            pass

    def _status_for(sub, start_field: str | None, end_field: str | None, status_field: str | None, is_active_field: str | None):
        if sub is None:
            return "none", "لا يوجد اشتراك", "bg-rose-50 text-rose-700"

        raw_status = _get_attr(sub, status_field) if status_field else None
        is_active_flag = _get_attr(sub, is_active_field) if is_active_field else None

        start_value = _get_attr(sub, start_field) if start_field else None
        end_value = _compute_display_ends_at(sub, start_field, end_field)

        if raw_status == "cancelled" or (raw_status is None and is_active_flag is False):
            return "cancelled", "ملغى", "bg-rose-50 text-rose-700"

        if raw_status == "pending":
            return "pending", "قيد الإعداد", "bg-amber-50 text-amber-700"

        if raw_status == "active" or (raw_status is None and is_active_flag is True):
            if start_value and start_value > today:
                return "upcoming", "لم يبدأ بعد", "bg-amber-50 text-amber-700"
            if end_value and end_value < today:
                return "expired", "منتهي", "bg-rose-50 text-rose-700"
            return "active", "سارية", "bg-emerald-50 text-emerald-700"

        if isinstance(raw_status, str) and raw_status:
            return raw_status, "غير معروف", "bg-slate-100 text-slate-700"

        return "unknown", "غير معروف", "bg-slate-100 text-slate-700"

    if SubModel is not None:
        start_field = "starts_at" if _field_exists(SubModel, "starts_at") else ("start_date" if _field_exists(SubModel, "start_date") else None)
        end_field = "ends_at" if _field_exists(SubModel, "ends_at") else ("end_date" if _field_exists(SubModel, "end_date") else None)
        status_field = "status" if _field_exists(SubModel, "status") else None
        is_active_field = "is_active" if _field_exists(SubModel, "is_active") else None

        qs = SubModel.objects.filter(school=school)

        # الاشتراك الحالي (ساري ضمن اليوم)
        current_qs = qs
        if status_field:
            current_qs = current_qs.filter(status="active")
        elif is_active_field:
            current_qs = current_qs.filter(is_active=True)
        if start_field:
            current_qs = current_qs.filter(**{f"{start_field}__lte": today})
        if end_field:
            current_qs = current_qs.filter(Q(**{f"{end_field}__isnull": True}) | Q(**{f"{end_field}__gte": today}))
        if start_field:
            current_qs = current_qs.order_by(f"-{start_field}", "-id")
        else:
            current_qs = current_qs.order_by("-id")
        current_subscription = current_qs.first()

        # الاشتراك القادم (أقرب اشتراك يبدأ في المستقبل)
        upcoming_qs = qs
        if status_field:
            upcoming_qs = upcoming_qs.filter(status__in=["active", "pending"])
        elif is_active_field:
            upcoming_qs = upcoming_qs.filter(is_active=True)
        if start_field:
            upcoming_qs = upcoming_qs.filter(**{f"{start_field}__gt": today}).order_by(start_field, "id")
            upcoming_subscription = upcoming_qs.first()

        _attach_display_fields(current_subscription, start_field, end_field)
        _attach_display_fields(upcoming_subscription, start_field, end_field)

        current_status_code, current_status_label, current_status_badge_class = _status_for(
            current_subscription,
            start_field,
            end_field,
            status_field,
            is_active_field,
        )

        primary_subscription = current_subscription or upcoming_subscription
        primary_label = "الاشتراك الحالي" if current_subscription else ("الاشتراك القادم" if upcoming_subscription else "")

    # ==========================
    # طلبات الاشتراك/التجديد
    # ==========================
    RequestModel = _get_subscription_request_model()
    renew_form = SubscriptionRenewalRequestForm(prefix="renew")
    new_form = SubscriptionNewRequestForm(prefix="new")

    def _shorten_receipt_filename(file_obj, *, prefix: str) -> Any:
        """Keep the stored ImageField path short (DB safety).

        Some production DBs may still have receipt_image as varchar(100) until migrations run.
        By shortening the uploaded filename we avoid 500s even before the ALTER migration.
        """
        if not file_obj:
            return file_obj
        original_name = (getattr(file_obj, "name", "") or "").strip()
        _base, ext = os.path.splitext(original_name)
        ext = (ext or ".jpg").lower()
        # keep extension sane
        if len(ext) > 10:
            ext = ext[:10]
        stamp = timezone.now().strftime("%Y%m%d%H%M%S")
        token = get_random_string(6)
        try:
            file_obj.name = f"{prefix}_{stamp}_{token}{ext}"
        except Exception:
            pass
        return file_obj

    # خطط الاشتراك (لإظهار تفاصيل الخطة في الواجهة)
    try:
        available_plans = list(
            SubscriptionPlan.objects.filter(is_active=True).order_by("sort_order", "name", "id")
        )
    except Exception:
        available_plans = []

    # تأكيد أن نموذج الاشتراك الجديد يستخدم نفس القائمة حتى عند POST
    try:
        new_form.fields["plan"].queryset = SubscriptionPlan.objects.filter(is_active=True).order_by(
            "sort_order", "name", "id"
        )
    except Exception:
        pass

    def _plan_details(plan_obj):
        if plan_obj is None:
            return None
        try:
            price = getattr(plan_obj, "price", None)
            duration_days = getattr(plan_obj, "duration_days", None)
            max_screens = getattr(plan_obj, "max_screens", None)
            return {
                "id": getattr(plan_obj, "pk", None),
                "name": getattr(plan_obj, "name", ""),
                "price": str(price) if price is not None else "",
                "duration_days": int(duration_days) if duration_days not in (None, "") else None,
                "max_screens": int(max_screens) if max_screens not in (None, "") else None,
            }
        except Exception:
            return None

    plans_map = {}
    for p in available_plans:
        d = _plan_details(p)
        if d and d.get("id") is not None:
            plans_map[str(d["id"])] = d

    renewal_plan_obj = None
    if current_subscription is not None:
        renewal_plan_obj = getattr(current_subscription, "plan", None)
    elif upcoming_subscription is not None:
        renewal_plan_obj = getattr(upcoming_subscription, "plan", None)
    elif primary_subscription is not None:
        renewal_plan_obj = getattr(primary_subscription, "plan", None)

    active_request_tab = "renewal" if renewal_plan_obj is not None else "new"

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action in {"renewal", "new"}:
            active_request_tab = action

        if RequestModel is None:
            messages.error(request, "ميزة طلبات الاشتراك غير متاحة حالياً.")
            return redirect("dashboard:my_subscription")

        if action not in {"renewal", "new"}:
            messages.error(request, "طلب غير صالح.")
            return redirect("dashboard:my_subscription")

        has_open = RequestModel.objects.filter(
            school=school,
            status__in=["submitted", "under_review"],
        ).exists()
        if has_open:
            messages.warning(request, "لديكم طلب قيد المراجعة بالفعل. الرجاء انتظار الرد قبل إرسال طلب جديد.")
            return redirect("dashboard:my_subscription")

        if action == "renewal":
            renew_form = SubscriptionRenewalRequestForm(request.POST, request.FILES, prefix="renew")
            if renew_form.is_valid():
                plan_obj = None
                if current_subscription is not None:
                    plan_obj = getattr(current_subscription, "plan", None)
                elif upcoming_subscription is not None:
                    plan_obj = getattr(upcoming_subscription, "plan", None)
                elif primary_subscription is not None:
                    plan_obj = getattr(primary_subscription, "plan", None)

                if plan_obj is None:
                    messages.error(request, "لا توجد خطة حالية يمكن تجديدها. استخدم طلب اشتراك جديد.")
                else:
                    RequestModel.objects.create(
                        school=school,
                        created_by=request.user,
                        request_type="renewal",
                        plan=plan_obj,
                        requested_starts_at=timezone.localdate(),
                        amount=getattr(plan_obj, "price", 0) or 0,
                        receipt_image=_shorten_receipt_filename(
                            renew_form.cleaned_data["receipt_image"],
                            prefix="renewal_receipt",
                        ),
                        transfer_note=renew_form.cleaned_data.get("transfer_note", "") or "",
                        status="submitted",
                    )
                    messages.success(request, "تم إرسال طلب التجديد بنجاح. سيتم مراجعته من الإدارة.")
                    return redirect("dashboard:my_subscription")
            else:
                messages.error(request, "الرجاء تصحيح الأخطاء في نموذج التجديد.")

        if action == "new":
            new_form = SubscriptionNewRequestForm(request.POST, request.FILES, prefix="new")
            try:
                new_form.fields["plan"].queryset = SubscriptionPlan.objects.filter(is_active=True).order_by(
                    "sort_order", "name", "id"
                )
            except Exception:
                pass
            if new_form.is_valid():
                plan_obj = new_form.cleaned_data["plan"]
                RequestModel.objects.create(
                    school=school,
                    created_by=request.user,
                    request_type="new",
                    plan=plan_obj,
                    requested_starts_at=timezone.localdate(),
                    amount=getattr(plan_obj, "price", 0) or 0,
                    receipt_image=_shorten_receipt_filename(
                        new_form.cleaned_data["receipt_image"],
                        prefix="new_receipt",
                    ),
                    transfer_note=new_form.cleaned_data.get("transfer_note", "") or "",
                    status="submitted",
                )
                messages.success(request, "تم إرسال طلب الاشتراك الجديد بنجاح. سيتم مراجعته من الإدارة.")
                return redirect("dashboard:my_subscription")
            messages.error(request, "الرجاء تصحيح الأخطاء في نموذج الاشتراك الجديد.")

    subscription_requests = []
    subscription_history = []
    if RequestModel is not None:
        try:
            subscription_requests = list(
                RequestModel.objects.filter(school=school)
                .select_related("plan", "processed_by", "approved_subscription")
                .order_by("-created_at", "-id")[:10]
            )
        except Exception:
            subscription_requests = []

    # ==========================
    # سجل العمليات: دمج الطلبات + الاشتراكات اليدوية
    # ==========================
    try:
        approved_sub_ids = set()
        if RequestModel is not None:
            approved_sub_ids = set(
                RequestModel.objects.filter(
                    school=school,
                    approved_subscription__isnull=False,
                ).values_list("approved_subscription_id", flat=True)
            )

        manual_subscriptions = []
        if SubModel is not None:
            manual_subscriptions = list(
                SubModel.objects.filter(school=school)
                .exclude(id__in=approved_sub_ids)
                .select_related("plan")
                .order_by("-created_at", "-id")[:10]
            )

        payment_ops_by_sub_id = {}
        try:
            from subscriptions.models import SubscriptionPaymentOperation

            sub_ids = [getattr(s, "pk", None) for s in manual_subscriptions if getattr(s, "pk", None) is not None]
            for r in subscription_requests:
                sid = getattr(r, "approved_subscription_id", None)
                if sid is not None:
                    sub_ids.append(sid)
            sub_ids = list({s for s in sub_ids if s is not None})

            if sub_ids:
                ops = (
                    SubscriptionPaymentOperation.objects.filter(
                        school=school,
                        subscription_id__in=sub_ids,
                    )
                    .prefetch_related("invoice")
                    .order_by("-created_at", "-id")
                )
                for op in ops:
                    sid = getattr(op, "subscription_id", None)
                    if sid and sid not in payment_ops_by_sub_id:
                        payment_ops_by_sub_id[sid] = op
        except Exception:
            payment_ops_by_sub_id = {}

        def _invoice_url_for_subscription_id(subscription_id: int | None) -> str | None:
            if not subscription_id:
                return None
            op = payment_ops_by_sub_id.get(subscription_id)
            if op is None:
                return None
            try:
                inv = getattr(op, "invoice", None)
            except Exception:
                inv = None
            if inv is None:
                return None
            try:
                return reverse("dashboard:subscription_invoice_view", kwargs={"pk": getattr(inv, "pk", None)})
            except Exception:
                return None

        for r in subscription_requests:
            receipt_url = None
            try:
                ri = getattr(r, "receipt_image", None)
                if ri and getattr(ri, "name", ""):
                    receipt_url = ri.url
            except Exception:
                receipt_url = None

            # طلبات الاشتراك الحالية تعتمد على رفع إيصال (تحويل بنكي)
            payment_method_label = "تحويل" if receipt_url else "—"
            try:
                amt = getattr(r, "amount", 0) or 0
                if float(amt) <= 0:
                    payment_method_label = "مجاني"
            except Exception:
                pass
            subscription_history.append(
                {
                    "date": getattr(r, "created_at", None),
                    "type_label": getattr(r, "get_request_type_display", lambda: "طلب")(),
                    "payment_method_label": payment_method_label,
                    "plan_name": getattr(getattr(r, "plan", None), "name", "—"),
                    "amount": getattr(r, "amount", None),
                    "status_code": getattr(r, "status", ""),
                    "status_label": getattr(r, "get_status_display", lambda: "")(),
                    "receipt_url": receipt_url,
                    "invoice_url": _invoice_url_for_subscription_id(getattr(r, "approved_subscription_id", None)),
                }
            )

        for s in manual_subscriptions:
            plan_obj = getattr(s, "plan", None)

            plan_price = 0
            try:
                plan_price = getattr(plan_obj, "price", 0) or 0
            except Exception:
                plan_price = 0

            payment_method_label = "غير محددة"
            try:
                if float(plan_price) <= 0:
                    payment_method_label = "مجاني"
                else:
                    op = payment_ops_by_sub_id.get(getattr(s, "pk", None))
                    if op is not None:
                        payment_method_label = getattr(op, "get_method_display", lambda: "غير محددة")()
            except Exception:
                pass

            subscription_history.append(
                {
                    "date": getattr(s, "created_at", None),
                    "type_label": "تفعيل يدوي",
                    "payment_method_label": payment_method_label,
                    "plan_name": getattr(plan_obj, "name", "—"),
                    "amount": plan_price,
                    "status_code": getattr(s, "status", ""),
                    "status_label": getattr(s, "get_status_display", lambda: "")(),
                    "receipt_url": None,
                    "invoice_url": _invoice_url_for_subscription_id(getattr(s, "pk", None)),
                }
            )

        safe_min_dt = timezone.make_aware(datetime(1970, 1, 1))
        subscription_history.sort(
            key=lambda x: (x.get("date") or safe_min_dt),
            reverse=True,
        )
        subscription_history = subscription_history[:10]
    except Exception:
        subscription_history = []

    return render(
        request,
        "dashboard/my_subscription.html",
        {
            "school": school,
            "subscription": primary_subscription,
            "primary_label": primary_label,

            "current_subscription": current_subscription,
            "current_status_code": current_status_code,
            "current_status_label": current_status_label,
            "current_status_badge_class": current_status_badge_class,

            "upcoming_subscription": upcoming_subscription,
            "today": today,

            "renew_form": renew_form,
            "new_form": new_form,
            "subscription_requests": subscription_requests,
            "subscription_history": subscription_history,

            "active_request_tab": active_request_tab,
            "renewal_plan_details": _plan_details(renewal_plan_obj),
            "plans_map": plans_map,
            "screens_total_count": screens_total_count,
            "screens_active_count": screens_active_count,
            "screens_live_count": screens_live_count,
            "screen_status_label": screen_status_label,
            "screen_status_class": screen_status_class,
        },
    )


@login_required
def subscription_invoice_view(request, pk: int):
    """عرض الفاتورة للعميل (حسب المدرسة النشطة)."""
    from django.http import HttpResponse

    from subscriptions.models import SubscriptionInvoice

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    invoice_qs = SubscriptionInvoice.objects.select_related("school", "subscription", "plan")
    if not request.user.is_superuser:
        # Tenant isolation belongs in the query itself so another school's
        # invoice is indistinguishable from a non-existent invoice.
        invoice_qs = invoice_qs.filter(school=school)
    invoice = get_object_or_404(invoice_qs, pk=pk)

    try:
        from django.template.loader import render_to_string

        from subscriptions.invoicing import _get_seller_info, _get_school_contact_info
    except Exception:
        render_to_string = None
        _get_seller_info = None
        _get_school_contact_info = None

    # للمدارس: نعرض الفاتورة ببيانات المستخدم الحالي حتى لا تظهر بيانات الآدمن.
    if not request.user.is_superuser and render_to_string and _get_seller_info and _get_school_contact_info:
        contact_name, contact_mobile = _get_school_contact_info(invoice.school, preferred_user=request.user)
        html = render_to_string(
            "invoices/subscription_invoice.html",
            {
                "invoice": invoice,
                "seller": _get_seller_info(),
                "school": invoice.school,
                "subscription": invoice.subscription,
                "plan": invoice.plan,
                "contact_name": contact_name,
                "contact_mobile": contact_mobile,
            },
        )
    else:
        html = (invoice.html_snapshot or "").strip()
        if not html and render_to_string and _get_seller_info and _get_school_contact_info:
            try:
                contact_name, contact_mobile = _get_school_contact_info(invoice.school)
                html = render_to_string(
                    "invoices/subscription_invoice.html",
                    {
                        "invoice": invoice,
                        "seller": _get_seller_info(),
                        "school": invoice.school,
                        "subscription": invoice.subscription,
                        "plan": invoice.plan,
                        "contact_name": contact_name,
                        "contact_mobile": contact_mobile,
                    },
                )
                invoice.html_snapshot = html
                invoice.save(update_fields=["html_snapshot"])
            except Exception:
                html = ""

    if not html:
        messages.error(request, "تعذر عرض الفاتورة حاليًا.")
        return redirect("dashboard:my_subscription")

    return HttpResponse(html, content_type="text/html; charset=utf-8")


# ==================
# Plans Management
# ==================

@login_required
@user_passes_test(lambda u: u.is_superuser)
def system_plans_list(request):
    plans = SubscriptionPlan.objects.all()
    return render(request, "admin/plans_list.html", {"plans": plans})

@login_required
@user_passes_test(lambda u: u.is_superuser)
def system_plan_create(request):
    if request.method == "POST":
        form = SubscriptionPlanForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إنشاء الخطة بنجاح")
            return redirect("dashboard:system_plans_list")
    else:
        form = SubscriptionPlanForm()
    return render(request, "admin/plan_form.html", {"form": form, "title": "إضافة خطة جديدة"})

@login_required
@user_passes_test(lambda u: u.is_superuser)
def system_plan_edit(request, pk):
    plan = get_object_or_404(SubscriptionPlan, pk=pk)
    if request.method == "POST":
        form = SubscriptionPlanForm(request.POST, instance=plan)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث الخطة بنجاح")
            return redirect("dashboard:system_plans_list")
    else:
        form = SubscriptionPlanForm(instance=plan)
    return render(request, "admin/plan_form.html", {"form": form, "title": "تعديل الخطة"})

@login_required
@user_passes_test(lambda u: u.is_superuser)
def system_plan_delete(request, pk):
    plan = get_object_or_404(SubscriptionPlan, pk=pk)
    if request.method == "POST":
        plan.delete()
        messages.success(request, "تم حذف الخطة بنجاح")
        return redirect("dashboard:system_plans_list")
    return render(request, "admin/plan_confirm_delete.html", {"plan": plan})

# ==================
# Reports
# ==================

@login_required
@system_staff_required
def system_reports(request):
    School = SchoolModel()
    SubModel = _get_subscription_model()

    schools_count = School.objects.count()
    total_revenue = 0
    active_subscriptions_count = 0
    total_subscriptions_count = 0
    avg_revenue_per_active = 0
    revenue_by_plan = []
    schools_distribution = []
    monthly_growth = []

    today = timezone.localdate()

    def _recent_months(count: int = 6):
        out = []
        for offset in range(count - 1, -1, -1):
            year = today.year
            month = today.month - offset
            while month <= 0:
                month += 12
                year -= 1
            out.append((year, month))
        return out

    month_keys = _recent_months(6)

    if SubModel is not None:
        all_subs = SubModel.objects.all()
        total_subscriptions_count = all_subs.count()

        active_subs = all_subs
        if _model_has_field(SubModel, "status"):
            active_subs = active_subs.filter(status="active")
        elif _model_has_field(SubModel, "is_active"):
            active_subs = active_subs.filter(is_active=True)

        if _model_has_field(SubModel, "starts_at"):
            active_subs = active_subs.filter(starts_at__lte=today)
        elif _model_has_field(SubModel, "start_date"):
            active_subs = active_subs.filter(start_date__lte=today)

        if _model_has_field(SubModel, "ends_at"):
            active_subs = active_subs.filter(Q(ends_at__isnull=True) | Q(ends_at__gte=today))
        elif _model_has_field(SubModel, "end_date"):
            active_subs = active_subs.filter(Q(end_date__isnull=True) | Q(end_date__gte=today))

        active_subscriptions_count = active_subs.count()
        total_revenue = active_subs.aggregate(total=Sum("plan__price")).get("total") or 0
        if active_subscriptions_count:
            avg_revenue_per_active = (total_revenue or 0) / active_subscriptions_count

        revenue_rows = (
            active_subs.values("plan__name")
            .annotate(total_revenue=Sum("plan__price"), count=Count("id"))
            .order_by("-total_revenue")
        )
        for row in revenue_rows:
            plan_name = (row.get("plan__name") or "غير محدد").strip()
            row_revenue = row.get("total_revenue") or 0
            share_percent = (float(row_revenue) / float(total_revenue) * 100) if total_revenue else 0
            revenue_by_plan.append(
                {
                    "plan_name": plan_name,
                    "count": int(row.get("count") or 0),
                    "total_revenue": row_revenue,
                    "share_percent": round(share_percent, 1),
                }
            )

        month_field = None
        for candidate in ("starts_at", "start_date", "created_at"):
            if _model_has_field(SubModel, candidate):
                month_field = candidate
                break

        monthly_map = {}
        if month_field:
            try:
                monthly_rows = (
                    all_subs.filter(**{f"{month_field}__isnull": False})
                    .annotate(month=TruncMonth(month_field))
                    .values("month")
                    .annotate(count=Count("id"))
                    .order_by("month")
                )
                for row in monthly_rows:
                    month_value = row.get("month")
                    if not month_value:
                        continue
                    monthly_map[(int(month_value.year), int(month_value.month))] = int(row.get("count") or 0)
            except Exception:
                monthly_map = {}

        monthly_growth = [
            {
                "label": f"{month:02d}/{year}",
                "count": int(monthly_map.get((year, month), 0)),
            }
            for year, month in month_keys
        ]

    if not monthly_growth:
        monthly_growth = [{"label": f"{month:02d}/{year}", "count": 0} for year, month in month_keys]

    if _model_has_field(School, "school_type"):
        try:
            type_field = School._meta.get_field("school_type")
            choices = {str(k): str(v) for k, v in (type_field.choices or [])}
            rows = School.objects.values("school_type").annotate(count=Count("id")).order_by("-count")
            for row in rows:
                school_type = row.get("school_type")
                school_type_str = "" if school_type is None else str(school_type)
                label = choices.get(school_type_str) or school_type_str or "غير محدد"
                schools_distribution.append(
                    {
                        "label": label,
                        "count": int(row.get("count") or 0),
                    }
                )
        except Exception:
            schools_distribution = []

    if not schools_distribution and schools_count:
        schools_distribution = [{"label": "إجمالي المدارس", "count": int(schools_count)}]

    schools_distribution_total = sum(int(item.get("count") or 0) for item in schools_distribution)
    for item in schools_distribution:
        count = int(item.get("count") or 0)
        item["percent"] = round((count / schools_distribution_total) * 100, 1) if schools_distribution_total else 0

    max_monthly_subscriptions = 0
    if monthly_growth:
        max_monthly_subscriptions = max(int(item.get("count") or 0) for item in monthly_growth)

    context = {
        "schools_count": schools_count,
        "total_revenue": total_revenue,
        "active_subscriptions_count": active_subscriptions_count,
        "total_subscriptions_count": total_subscriptions_count,
        "avg_revenue_per_active": avg_revenue_per_active,
        "revenue_by_plan": revenue_by_plan,
        "monthly_growth": monthly_growth,
        "max_monthly_subscriptions": max_monthly_subscriptions,
        "schools_distribution": schools_distribution,
        "schools_distribution_total": schools_distribution_total,
    }
    return render(request, "admin/reports.html", context)

def _get_screen_addon_model():
    try:
        return apps.get_model("subscriptions", "SubscriptionScreenAddon")
    except Exception:
        return None


def _get_subscription_request_model():
    try:
        return apps.get_model("subscriptions", "SubscriptionRequest")
    except Exception:
        return None


@system_staff_required
def system_screen_addons_list(request):
    Addon = _get_screen_addon_model()
    if Addon is None:
        messages.error(request, "نظام زيادات الشاشات غير مثبت.")
        return redirect("dashboard:system_subscriptions_list")

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()

    qs = Addon.objects.all()
    try:
        qs = qs.select_related("subscription", "subscription__school", "subscription__plan")
    except Exception:
        pass

    if q:
        qs = qs.filter(
            Q(subscription__school__name__icontains=q)
            | Q(subscription__school__slug__icontains=q)
            | Q(subscription__plan__name__icontains=q)
        )

    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-created_at", "-id")

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    rows = []
    for obj in page_obj.object_list:
        sub = getattr(obj, "subscription", None)
        school_obj = getattr(sub, "school", None) if sub else None
        plan_obj = getattr(sub, "plan", None) if sub else None
        rows.append(
            {
                "id": obj.pk,
                "school_name": getattr(school_obj, "name", "—") if school_obj else "—",
                "plan_name": getattr(plan_obj, "name", "—") if plan_obj else "—",
                "screens_added": getattr(obj, "screens_added", 0) or 0,
                "pricing_cycle": getattr(obj, "pricing_cycle", "inherit") or "inherit",
                "validity_days": getattr(obj, "validity_days", None),
                "starts_at": getattr(obj, "starts_at", None),
                "ends_at": getattr(obj, "ends_at", None),
                "status": getattr(obj, "status", ""),
                "total_price": getattr(obj, "total_price", None),
            }
        )

    return render(
        request,
        "admin/screen_addons_list.html",
        {
            "rows": rows,
            "page_obj": page_obj,
            "q": q,
            "status": status,
        },
    )


@system_staff_required
def system_screen_addon_create(request):
    Addon = _get_screen_addon_model()
    if Addon is None:
        messages.error(request, "نظام زيادات الشاشات غير مثبت.")
        return redirect("dashboard:system_screen_addons_list")

    if request.method == "POST":
        form = SubscriptionScreenAddonForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "تم إنشاء زيادة الشاشات بنجاح.")
            return redirect("dashboard:system_screen_addons_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = SubscriptionScreenAddonForm()

    return render(request, "admin/screen_addon_form.html", {"form": form, "title": "إضافة زيادة شاشات"})


@system_staff_required
def system_screen_addon_edit(request, pk: int):
    Addon = _get_screen_addon_model()
    if Addon is None:
        messages.error(request, "نظام زيادات الشاشات غير مثبت.")
        return redirect("dashboard:system_screen_addons_list")

    obj = get_object_or_404(Addon, pk=pk)

    if request.method == "POST":
        form = SubscriptionScreenAddonForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث زيادة الشاشات.")
            return redirect("dashboard:system_screen_addons_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")
    else:
        form = SubscriptionScreenAddonForm(instance=obj)

    return render(
        request,
        "admin/screen_addon_form.html",
        {"form": form, "title": "تعديل زيادة شاشات", "edit": True, "obj": obj},
    )


@superuser_required
def system_screen_addon_delete(request, pk: int):
    Addon = _get_screen_addon_model()
    if Addon is None:
        messages.error(request, "نظام زيادات الشاشات غير مثبت.")
        return redirect("dashboard:system_screen_addons_list")

    obj = get_object_or_404(Addon, pk=pk)
    if request.method == "POST":
        obj.delete()
        messages.warning(request, "تم حذف زيادة الشاشات.")
        return redirect("dashboard:system_screen_addons_list")

    return render(request, "admin/screen_addon_confirm_delete.html", {"obj": obj})

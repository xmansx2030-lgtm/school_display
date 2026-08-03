from __future__ import annotations
from django.db.models import Q

from importlib import import_module
from typing import Any, Callable, Dict, List, Tuple
import secrets

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.apps import apps
from core.display_presence import touch_display_presence

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ١) أدوات مساعدة مرنة للجدول الدراسي (schedule)
# ---------------------------------------------------------------------------

def _fallback_today_state(school: Any) -> Dict[str, Any]:
    """
    دالة احتياطية تُستخدم إذا لم نجد دوال الجدول في تطبيق schedule.

    ترجع بنية بسيطة تكفي الواجهة حتى لا تتعطل.
    """
    now = timezone.localtime()
    return {
        "state": {
            "type": "unknown",
            "label": "لا يوجد جدول لهذا اليوم",
        },
        "day": {
            "name": now.strftime("%A"),
            "date": now.date().isoformat(),
        },
        "periods": [],
        "breaks": [],
        "settings": {},
        "date_info": {
            "gregorian": now.strftime("%Y-%m-%d"),
        },
    }


def _fallback_period_classes_now(school: Any) -> List[Dict[str, Any]]:
    """
    دالة احتياطية تُستخدم إذا لم نجد get_period_classes_now.
    ترجع قائمة فاضية بدون كسر الواجهة.
    """
    return []


def _get_schedule_helpers() -> Tuple[
    Callable[[Any], Dict[str, Any]],
    Callable[[Any], List[Dict[str, Any]]],
]:
    """
    يحاول استيراد دوال الجدول من schedule.utils بشكل مرن.

    - إذا وُجدت get_today_state و get_period_classes_now → تُستخدم.
    - إذا لم توجد أو حدث خطأ → نستخدم دوال احتياطية لا تكسر النظام.
    """
    try:
        utils_mod = import_module("schedule.utils")
    except Exception:
        logger.warning(
            "schedule.utils غير موجود؛ سيتم استخدام دوال احتياطية لشاشة العرض.",
            exc_info=True,
        )
        return _fallback_today_state, _fallback_period_classes_now

    get_today_state = getattr(utils_mod, "get_today_state", None)
    get_period_classes_now = getattr(utils_mod, "get_period_classes_now", None)

    if callable(get_today_state) and callable(get_period_classes_now):
        return get_today_state, get_period_classes_now

    logger.warning(
        "schedule.utils موجود لكن الدوال get_today_state/get_period_classes_now غير معرفة "
        "أو غير قابلة للاستدعاء؛ سيتم استخدام دوال احتياطية."
    )
    return _fallback_today_state, _fallback_period_classes_now


# ---------------------------------------------------------------------------
# ٢) أدوات مساعدة مرنة للإعلانات وحصص الانتظار والمتميزين
# ---------------------------------------------------------------------------

# إعلانــات
try:
    from notices.models import Announcement  # type: ignore
except Exception:  # pragma: no cover - يعتمد على تركيب المشروع
    Announcement = None  # type: ignore
    logger.info("تعذر استيراد notices.Announcement؛ سيتم إرجاع قائمة إعلانات فارغة في الـ API.")


def _get_announcements_for_school(school: Any) -> List[Dict[str, Any]]:
    """
    إرجاع الإعلانات النشطة للمدرسة بصيغة قوائم قواميس.

    المنطق:
      - إذا كان في Announcement.active_for_school(school) → نستخدمها.
      - إذا ما فيه، نحاول استخدام Announcement.objects.filter(...) بشكل عام.
      - لو حدث أي خطأ → نرجع قائمة فاضية بدون كسر النظام.
    """
    if Announcement is None:
        return []

    # 1) لو عندنا ميثود مخصّص active_for_school
    active_for_school = getattr(Announcement, "active_for_school", None)
    if callable(active_for_school):
        try:
            qs = active_for_school(school)
        except Exception:
            logger.exception("تعذر جلب الإعلانات من Announcement.active_for_school.")
            return []
    else:
        # 2) Fallback عام: نفترض وجود حقل is_active وربما school
        try:
            qs = Announcement.objects.all()
            if hasattr(Announcement, "is_active"):
                qs = qs.filter(is_active=True)
            if hasattr(Announcement, "school"):
                qs = qs.filter(school=school)
        except Exception:
            logger.exception("تعذر جلب الإعلانات من Announcement.objects.")
            return []

    items: List[Dict[str, Any]] = []
    for a in qs:
        as_dict = getattr(a, "as_dict", None)
        if callable(as_dict):
            try:
                items.append(as_dict())
                continue
            except Exception:
                logger.exception("خطأ أثناء استدعاء Announcement.as_dict().")

        items.append(
            {
                "id": getattr(a, "id", None),
                "title": getattr(a, "title", "") or "",
                "text": getattr(a, "text", "") or "",
            }
        )
    return items



# حصص الانتظار
try:
    from standby import models as standby_models  # type: ignore
except Exception:  # pragma: no cover
    standby_models = None
    logger.info(
        "تعذر استيراد standby.models؛ سيتم إرجاع قائمة حصص انتظار فارغة في شاشة العرض."
    )


def _get_standby_items_for_school(school: Any) -> List[Dict[str, Any]]:
    """
    إرجاع حصص الانتظار لليوم.

    - تحاول استخدام StandbySlot.active_for_today(school) إن وُجدت.
    - لو لم توجد StandbySlot أو حدث خطأ → ترجع قائمة فاضية بدون كسر النظام.
    """
    if standby_models is None:
        return []

    slot_cls = getattr(standby_models, "StandbySlot", None)
    if slot_cls is None:
        # في مشروعك الحالي قد يكون اسم الموديل مختلف (Standby, StandbyLesson, ...إلخ).
        # بما أننا لا نعرفه بدقة، نخليها فاضية أفضل من كسر السيرفر.
        logger.debug("لم يتم العثور على StandbySlot داخل standby.models؛ سيتم إرجاع قائمة فاضية.")
        return []

    try:
        # لو فيه دالة classmethod/manager مخصّصة
        active_for_today = getattr(slot_cls, "active_for_today", None)
        if callable(active_for_today):
            qs = active_for_today(school)
        else:
            # fallback عام؛ قد لا يُستخدم في تركيبك لكن لا يضر
            qs = slot_cls.objects.filter(is_active=True)

        items: List[Dict[str, Any]] = []
        for s in qs:
            as_dict = getattr(s, "as_dict", None)
            if callable(as_dict):
                try:
                    items.append(as_dict())
                    continue
                except Exception:
                    logger.exception("خطأ أثناء استدعاء StandbySlot.as_dict().")

            # fallback مبسط لأهم الحقول المحتملة
            items.append(
                {
                    "teacher": getattr(s, "teacher_name", "")
                    or str(getattr(s, "teacher", "")),
                    "class": getattr(s, "class_name", "") or getattr(s, "klass", ""),
                    "room": getattr(s, "room", ""),
                    "period": getattr(s, "period_label", ""),
                }
            )
        return items
    except Exception:
        logger.exception("تعذر جلب حصص الانتظار من standby.")
        return []


# المتميزون / لوحة الشرف — في مشروعك الحالي لا يوجد موديل مخصص، فنرجع دائمًا قائمة فاضية
def _get_excellence_items_for_school(school: Any) -> List[Dict[str, Any]]:
    """
    إرجاع عناصر لوحة الشرف (لوحة المتميزين).
    في هذا المشروع لا يوجد موديل خاص، لذلك نعيد قائمة فاضية بشكل افتراضي.
    """
    return []


# ---------------------------------------------------------------------------
# ٣) منطق اختيار معدل التحديث الذكي
# ---------------------------------------------------------------------------

def _compute_refresh_hint(today_state: Dict[str, Any]) -> int:
    """
    يحسب معدل التحديث المقترح (بالثواني) بناءً على حالة اليوم.

    المبدأ:
      - أثناء الحصص / الاستراحة / قبل بدء الطابور -> تحديث سريع (10 ثواني).
      - بعد انتهاء اليوم الدراسي -> تحديث متوسّط (30 دقيقة).
      - في الإجازات / يوم بلا دوام -> تحديث بطيء (180 دقيقة).
      - غير ذلك -> قيمة افتراضية آمنة (60 ثانية).

    هذا مجرد "تلميح" للواجهة ولا يغيّر إعدادات المستخدم في لوحة التحكم.
    """
    state = today_state.get("state") or {}
    state_type = (state.get("type") or "").lower().strip()

    if state_type in {"before", "period", "break"}:
        return 10  # ثواني
    if state_type == "after":
        return 30 * 60  # 30 دقيقة
    if state_type == "off":
        return 180 * 60  # 180 دقيقة
    return 60  # افتراضي


# ---------------------------------------------------------------------------
# ٤) بناء الحمولة الأساسية للـ Snapshot
# ---------------------------------------------------------------------------

def _build_snapshot_payload(school: Any) -> Dict[str, Any]:
    """
    يبني الحمولة الأساسية للـ snapshot لشاشة العرض لمدرسة معينة في يوم معيّن.

    ترجع بنية JSON متسقة مع display.js:
      - today:           حالة اليوم + الجدول + معلومات التاريخ.
      - period_classes:  الحصص الجارية الآن.
      - ann:             الإعلانات النشطة.
      - standby:         حصص الانتظار (standby.items).
      - exc:             المتميزون (exc.items).
      - settings:        حجز لخيارات مستقبلية (حاليًا None).
      - server_time:     وقت السيرفر (ISO).
      - refresh_hint_seconds: تلميح لمعدل التحديث.
    """
    get_today_state, get_period_classes_now = _get_schedule_helpers()

    # الحالة العامة لليوم + الجدول
    today_state: Dict[str, Any] = get_today_state(school)

    # الحصص الجارية الآن
    period_classes: Any = get_period_classes_now(school)

    # الإعلانات
    ann_items = _get_announcements_for_school(school)

    # حصص الانتظار
    standby_items = _get_standby_items_for_school(school)

    # لوحة الشرف / المتميزون (حاليًا فارغ افتراضيًا)
    exc_items = _get_excellence_items_for_school(school)

    # تلميح لمعدل التحديث الذكي
    refresh_hint_seconds = _compute_refresh_hint(today_state)

    # إعدادات العرض (الاسم، الشعار، نوع المدرسة...)
    settings_dict = {
        "name": getattr(school, "name", ""),
        "school_type": getattr(school, "school_type", ""),
    }
    if hasattr(school, "logo") and school.logo:
        settings_dict["logo_url"] = getattr(school.logo, "url", "")

    payload: Dict[str, Any] = {
        "today": today_state,
        "period_classes": period_classes,
        "ann": ann_items,
        "standby": {"items": standby_items},
        "exc": {"items": exc_items},
        "settings": settings_dict,
        "server_time": timezone.now().isoformat(),
        "refresh_hint_seconds": refresh_hint_seconds,
    }
    return payload


# ---------------------------------------------------------------------------
# ٥) نقطة الـ API الفعلية
# ---------------------------------------------------------------------------

import re
from django.db.models import Q

TOKEN_RE = re.compile(r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{64})$")

@require_GET
def display_snapshot(request: HttpRequest, token: str) -> HttpResponse:
    """
    /api/display/snapshot/<token>/
    """
    token = (token or "").strip()

    # ✅ تحقق صارم للتوكن (أوضح وأضمن)
    if not TOKEN_RE.match(token):
        return JsonResponse({"error": "Invalid token."}, status=400)

    # نحاول الحصول على شاشة العرض عبر token بمرونة حسب أسماء الحقول
    DisplayScreen = None
    for app_label in ("core", "dashboard"):
        try:
            DisplayScreen = apps.get_model(app_label, "DisplayScreen")
            break
        except Exception:
            continue

    if DisplayScreen is None:
        return JsonResponse({"error": "DisplayScreen model not found."}, status=500)

    # حاول حقول شائعة: api_token / token / access_token / short_code
    q = Q()
    for field in ("api_token", "token", "access_token", "short_code"):
        try:
            DisplayScreen._meta.get_field(field)
            # ✅ مطابقة غير حساسة لحالة الأحرف
            q |= Q(**{f"{field}__iexact": token})
        except Exception:
            continue

    if not q:
        return JsonResponse({"error": "No token field found on DisplayScreen."}, status=500)

    qs = DisplayScreen.objects.select_related("school").filter(q)

    # ✅ لو عندك is_active في الموديل
    try:
        DisplayScreen._meta.get_field("is_active")
        qs = qs.filter(is_active=True)
    except Exception:
        pass

    screen = qs.first()
    if screen is None or getattr(screen, "school", None) is None:
        return JsonResponse({"error": "Token not found or screen has no school."}, status=404)

    # ------------------------------------------------------------
    # ✅ منع مشاركة رابط الشاشة على أكثر من جهاز (TV Binding)
    #    لو لم يوجد كوكي للجهاز ننشئ واحدًا تلقائيًا
    # ------------------------------------------------------------
    new_device_id: str | None = None
    device_id = (request.COOKIES.get("sd_device") or "").strip()
    if not device_id:
        try:
            new_device_id = secrets.token_hex(16)
            device_id = new_device_id
            request.COOKIES["sd_device"] = device_id
        except Exception:
            device_id = ""

    # نتأكد أن الموديل يدعم الربط (لتوافق البيئات القديمة)
    has_binding_fields = True
    try:
        DisplayScreen._meta.get_field("bound_device_id")
        DisplayScreen._meta.get_field("bound_at")
    except Exception:
        has_binding_fields = False

    if device_id and has_binding_fields:
        bound = (getattr(screen, "bound_device_id", None) or "").strip()
        if not bound:
            # ربط ذري لمنع سباق جهازين في نفس اللحظة
            updated = (
                DisplayScreen.objects.filter(pk=screen.pk)
                .filter(Q(bound_device_id__isnull=True) | Q(bound_device_id=""))
                .update(bound_device_id=device_id, bound_at=timezone.now())
            )
            if updated == 0:
                # تم الربط من جهاز آخر قبلنا
                screen = DisplayScreen.objects.select_related("school").get(pk=screen.pk)
                bound = (getattr(screen, "bound_device_id", None) or "").strip()

        if bound and bound != device_id:
            return JsonResponse(
                {
                    "error": "screen_bound",
                    "message": "هذه الشاشة مفعّلة حاليًا على جهاز آخر. لفك الارتباط وتفعيلها هنا، انتقل إلى لوحة التحكم ثم شاشات العرض.",
                },
                status=403,
            )

    now = timezone.now()
    touch_display_presence(screen.pk, token=token, seen_at=now)

    school = screen.school
    request.school = school  # optional: يبقي التوافق مع أي كود يعتمد request.school

    today = timezone.localdate()
    cache_key = f"display:snapshot:{school.id}:{today.isoformat()}"

    cached = cache.get(cache_key)
    if cached is not None:
        cached = dict(cached)
        cached["server_time"] = now.isoformat()
        response = JsonResponse(cached)
    else:
        data = _build_snapshot_payload(school)
        cache.set(cache_key, data, timeout=10)
        response = JsonResponse(data)

    if new_device_id and hasattr(response, "set_cookie"):
        try:
            response.set_cookie(
                "sd_device",
                new_device_id,
                max_age=60 * 60 * 24 * 365 * 5,
                samesite="Lax",
            )
        except Exception:
            pass

    return response

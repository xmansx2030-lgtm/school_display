from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.auth import get_user_model, password_validation
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import DisplayScreen, School, SubscriptionPlan, UserProfile
from schedule.models import SchoolSettings
from subscriptions.models import SchoolSubscription


TRIAL_PLAN_CODE = "free-trial"
TRIAL_DAYS = 14
TRIAL_MAX_SCREENS = 1

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


@dataclass(frozen=True)
class TrialSignupResult:
    user: object
    school: School
    subscription: SchoolSubscription
    screen: DisplayScreen


class TrialSignupError(Exception):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("trial signup validation failed")


def normalize_mobile(value: str) -> str:
    raw = (value or "").translate(_ARABIC_DIGITS)
    digits = re.sub(r"\D+", "", raw)

    if digits.startswith("00966"):
        digits = "0" + digits[5:]
    elif digits.startswith("966"):
        digits = "0" + digits[3:]

    return digits


def _clean_text(value: str, *, max_length: int) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    return cleaned[:max_length]


def _trial_rate_limit_key(request, mobile: str) -> str:
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    ip = forwarded or request.META.get("REMOTE_ADDR") or "unknown"
    return f"trial-signup:{ip}:{mobile or 'missing'}"


def check_trial_rate_limit(request, mobile: str) -> None:
    key = _trial_rate_limit_key(request, mobile)
    try:
        added = cache.add(key, 1, timeout=60 * 60)
    except Exception:
        # Rate limiting should never be the reason a legitimate school cannot
        # start a trial if the cache backend is temporarily unavailable.
        return
    if added:
        return

    try:
        attempts = cache.incr(key)
    except Exception:
        attempts = 2
        cache.set(key, attempts, timeout=60 * 60)

    if attempts > 5:
        raise TrialSignupError(
            {"__all__": "تم إرسال عدة محاولات خلال وقت قصير. الرجاء المحاولة بعد ساعة."}
        )


def _school_type_value(value: str) -> str:
    value = (value or "").strip().lower()
    if value in {"boys", "بنين", "male"}:
        return "boys"
    if value in {"girls", "بنات", "female"}:
        return "girls"
    return ""


def _make_unique_slug(school_name: str) -> str:
    base = "trial"
    ascii_words = re.findall(r"[a-zA-Z0-9]+", (school_name or "").lower())
    if ascii_words:
        base = "-".join(ascii_words[:4])[:38].strip("-") or "trial"

    for _ in range(40):
        slug = f"{base}-{secrets.token_hex(3)}"
        if not School.objects.filter(slug=slug).exists():
            return slug

    return f"trial-{secrets.token_hex(8)}"


def _make_username(mobile: str) -> str:
    suffix = mobile[-9:] if mobile else secrets.token_hex(4)
    base = f"trial_{suffix}"
    UserModel = get_user_model()
    if not UserModel.objects.filter(username=base).exists():
        return base

    for _ in range(40):
        username = f"{base}_{secrets.token_hex(2)}"
        if not UserModel.objects.filter(username=username).exists():
            return username

    return f"trial_{secrets.token_hex(8)}"


def get_or_create_trial_plan() -> SubscriptionPlan:
    defaults = {
        "name": "تجربة مجانية 14 يوم",
        "price": Decimal("0.00"),
        "duration_days": TRIAL_DAYS,
        "max_users": 1,
        "max_screens": TRIAL_MAX_SCREENS,
        "max_schools": 1,
        "is_active": True,
        "sort_order": 0,
    }

    plan = SubscriptionPlan.objects.filter(code=TRIAL_PLAN_CODE).first()
    if plan is None:
        plan = (
            SubscriptionPlan.objects.filter(price=0, duration_days__in=[TRIAL_DAYS, TRIAL_DAYS + 1])
            .order_by("sort_order", "id")
            .first()
        )

    if plan is None:
        plan, _created = SubscriptionPlan.objects.get_or_create(
            code=TRIAL_PLAN_CODE,
            defaults=defaults,
        )

    # The fixed landing-page trial plan should stay aligned with the promise
    # shown to the visitor: 14 days and one screen.
    enforce_defaults = getattr(plan, "code", "") == TRIAL_PLAN_CODE
    changed = []
    fields_to_check = defaults.items() if enforce_defaults else [
        ("price", Decimal("0.00")),
        ("duration_days", TRIAL_DAYS),
        ("max_screens", TRIAL_MAX_SCREENS),
        ("is_active", True),
    ]
    for field, value in fields_to_check:
        if getattr(plan, field) != value:
            setattr(plan, field, value)
            changed.append(field)
    if changed:
        plan.save(update_fields=changed)
    return plan


def _validate_signup_data(data: dict) -> dict:
    school_name = _clean_text(data.get("school_name", ""), max_length=150)
    city = _clean_text(data.get("city", ""), max_length=80)
    contact_name = _clean_text(data.get("contact_name", ""), max_length=120)
    mobile = normalize_mobile(data.get("mobile", ""))
    school_type = _school_type_value(data.get("school_type", ""))
    password = data.get("password") or ""
    password_confirm = data.get("password_confirm") or ""
    website = (data.get("website") or "").strip()

    errors: dict[str, str] = {}
    if website:
        errors["__all__"] = "تعذر استقبال الطلب. الرجاء إعادة المحاولة."
    if len(school_name) < 3:
        errors["school_name"] = "اكتب اسم المدرسة بشكل أوضح."
    if school_type not in {"boys", "girls"}:
        errors["school_type"] = "اختر نوع المدرسة."
    if len(city) < 2:
        errors["city"] = "اكتب اسم المدينة."
    if len(contact_name) < 3:
        errors["contact_name"] = "اكتب اسم المسؤول."
    if not re.fullmatch(r"05\d{8}", mobile):
        errors["mobile"] = "اكتب رقم جوال سعودي صحيح بصيغة 05XXXXXXXX."
    if password != password_confirm:
        errors["password_confirm"] = "تأكيد كلمة المرور غير مطابق."

    UserModel = get_user_model()
    if mobile and UserProfile.objects.filter(mobile=mobile).exists():
        errors["mobile"] = "يوجد حساب مسجل بهذا الرقم. يمكنك تسجيل الدخول أو التواصل معنا للمساعدة."
    if mobile and UserModel.objects.filter(username=f"trial_{mobile[-9:]}").exists():
        errors["mobile"] = "يوجد حساب مسجل بهذا الرقم. يمكنك تسجيل الدخول أو التواصل معنا للمساعدة."

    if errors:
        raise TrialSignupError(errors)

    user_for_validation = UserModel(username=_make_username(mobile), first_name=contact_name[:150])
    try:
        password_validation.validate_password(password, user_for_validation)
    except ValidationError as exc:
        raise TrialSignupError({"password": " ".join(exc.messages)})

    return {
        "school_name": school_name,
        "city": city,
        "contact_name": contact_name,
        "mobile": mobile,
        "school_type": school_type,
        "password": password,
        "username": user_for_validation.username,
    }


@transaction.atomic
def create_trial_signup(data: dict) -> TrialSignupResult:
    cleaned = _validate_signup_data(data)
    plan = get_or_create_trial_plan()
    today = timezone.localdate()

    UserModel = get_user_model()

    try:
        school = School.objects.create(
            name=cleaned["school_name"],
            slug=_make_unique_slug(cleaned["school_name"]),
            is_active=True,
            school_type=cleaned["school_type"],
        )

        user = UserModel.objects.create_user(
            username=cleaned["username"],
            password=cleaned["password"],
            first_name=cleaned["contact_name"][:150],
            is_active=True,
        )

        profile, _created = UserProfile.objects.get_or_create(user=user)
        profile.schools.add(school)
        profile.active_school = school
        profile.mobile = cleaned["mobile"]
        profile.save(update_fields=["active_school", "mobile"])

        theme = "emerald" if cleaned["school_type"] == "boys" else "rose"
        SchoolSettings.objects.create(
            school=school,
            name=cleaned["school_name"],
            theme=theme,
            timezone_name="Asia/Riyadh",
            refresh_interval_sec=30,
        )

        subscription = SchoolSubscription.objects.create(
            school=school,
            plan=plan,
            starts_at=today,
            status="active",
            notes=(
                "Free trial from landing page"
                f" | city: {cleaned['city']}"
                f" | contact: {cleaned['contact_name']}"
                f" | mobile: {cleaned['mobile']}"
            ),
        )

        screen = DisplayScreen.objects.create(
            school=school,
            name="الشاشة الرئيسية",
            is_active=True,
        )

    except IntegrityError:
        raise TrialSignupError({"__all__": "تعذر إنشاء الحساب بسبب تعارض في البيانات. الرجاء المحاولة مرة أخرى."})

    return TrialSignupResult(user=user, school=school, subscription=subscription, screen=screen)

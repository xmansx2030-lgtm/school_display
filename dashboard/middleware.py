# dashboard/middleware.py
from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import resolve, Resolver404
from django.utils import timezone
from django.conf import settings

from subscriptions.utils import school_has_active_subscription
from core.two_factor import get_enabled_config, two_factor_required_for


class TwoFactorRequiredMiddleware:
    """Force privileged authenticated users to enroll before using the system."""

    EXEMPT_PREFIXES = (
        "/static/",
        "/media/",
        "/api/display/",
        "/dashboard/2fa/",
        "/dashboard/login/",
        "/dashboard/logout/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = str(getattr(request, "path", "") or "")
        if any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if (
            user
            and getattr(user, "is_authenticated", False)
            and two_factor_required_for(user)
            and get_enabled_config(user) is None
        ):
            return redirect("dashboard:two_factor_setup")
        return self.get_response(request)


class SubscriptionRequiredMiddleware:
    """
    يتحقق أن المدرسة النشطة (request.school) لديها اشتراك فعال قبل دخول /dashboard/

    - يعمل فقط على /dashboard/
    - يستثني: login/logout/demo_login/my_subscription/switch_school
    - لا يطبق على superuser ولا على غير المسجلين
    - يمنع تكرار رسالة الخطأ في نفس الجلسة
    """

    EXEMPT_VIEWS = {
        "dashboard:login",
        "dashboard:logout",
        "dashboard:demo_login",
        "dashboard:my_subscription",
        "dashboard:switch_school",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.process_request(request)
        if response is not None:
            return response
        return self.get_response(request)

    def process_request(self, request):
        if getattr(settings, "DEBUG", False) or getattr(settings, "MIDDLEWARE_DEBUG", False):
            print(
                f"[DEBUG:middleware] user={getattr(request, 'user', None)} | path={getattr(request, 'path', None)} | is_authenticated={getattr(request.user, 'is_authenticated', None)} | is_superuser={getattr(request.user, 'is_superuser', None)}"
            )
        path = request.path or ""

        if path.startswith("/static/") or path.startswith("/media/"):
            return None

        if not path.startswith("/dashboard/"):
            return None

        if not request.user.is_authenticated:
            return None

        # السماح الفوري للمستخدم الخارق (superuser) بدون أي تحقق آخر
        if getattr(request.user, "is_superuser", False):
            return None

        # موظف الدعم (Support group) لا يخضع لشرط اشتراك مدرسة
        try:
            if request.user.groups.filter(name="Support").exists():
                return None
        except Exception:
            pass

        try:
            match = resolve(request.path_info)
            view_name = match.view_name or ""
        except Resolver404:
            view_name = ""

        if view_name in self.EXEMPT_VIEWS:
            return None

        school = getattr(request, "school", None)
        if not school:
            # لا تمنع هنا حتى لا تعمل لوب؛
            # خلي الفيوهات تتعامل مع عدم وجود مدرسة عند الحاجة.
            return None

        today = timezone.localdate()
        has_active = school_has_active_subscription(school_id=school.id, on_date=today)

        if has_active:
            request.session.pop("sub_blocked_once", None)
            return None

        if not request.session.get("sub_blocked_once"):
            messages.error(request, "⚠️ اشتراك مدرستكم منتهي أو غير نشط. الرجاء مراجعة صفحة الاشتراك.")
            request.session["sub_blocked_once"] = True

        return redirect("dashboard:my_subscription")


class SupportDashboardOnlyMiddleware:
    """Restrict Support employees to the SaaS admin panel, excluding Schools.

    Goal (per product requirement):
    - Support users can use the system admin panel features.
    - Schools management must be hidden/blocked for them.
    - If they navigate outside the admin panel, redirect them back to the system dashboard.

    Notes:
    - Superusers are NOT restricted.
    - Static/media and display API endpoints are excluded.
    """

    ADMIN_PANEL_PREFIX = "/dashboard/admin-panel/"

    EXEMPT_VIEW_NAMES = {
        "dashboard:login",
        "dashboard:logout",
        "dashboard:change_password",
        "dashboard:switch_school",
        "dashboard:two_factor_setup",
        "dashboard:two_factor_verify",
    }

    FORBIDDEN_VIEW_NAMES = {
        "dashboard:system_schools_list",
        "dashboard:system_school_create",
        "dashboard:system_school_edit",
        "dashboard:system_school_delete",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = (getattr(request, "path", "") or "").strip()

        if not path:
            return self.get_response(request)

        # Always allow static/media and display API
        if path.startswith("/static/") or path.startswith("/media/") or path.startswith("/api/display/"):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if not user or not getattr(user, "is_authenticated", False):
            return self.get_response(request)

        if getattr(user, "is_superuser", False):
            return self.get_response(request)

        try:
            is_support = user.groups.filter(name="Support").exists()
        except Exception:
            is_support = False

        if not is_support:
            # A school manager may reach an internal admin URL from an old
            # bookmark or browser history. Send them back to their own panel
            # with a useful explanation instead of exposing Django's raw 403.
            if path.startswith(self.ADMIN_PANEL_PREFIX):
                try:
                    profile = user.profile
                    is_school_manager = profile.schools.exists()
                except Exception:
                    is_school_manager = False

                if is_school_manager:
                    messages.warning(
                        request,
                        "هذه الصفحة مخصصة لفريق إدارة النظام. تمت إعادتك إلى لوحة مدرستك.",
                    )
                    return redirect("dashboard:index")
            return self.get_response(request)

        # Resolve view name once.
        try:
            match = resolve(request.path_info)
            view_name = match.view_name or ""
        except Resolver404:
            view_name = ""

        # Always allow auth-related pages.
        if view_name in self.EXEMPT_VIEW_NAMES:
            return self.get_response(request)

        # Explicitly block schools pages for Support.
        if view_name in self.FORBIDDEN_VIEW_NAMES:
            return redirect("dashboard:system_admin_dashboard")

        # Allow anything within the admin panel.
        if path.startswith(self.ADMIN_PANEL_PREFIX) or view_name == "dashboard:system_admin_dashboard":
            return self.get_response(request)

        # Anything else: force back to the system admin dashboard.
        return redirect("dashboard:system_admin_dashboard")

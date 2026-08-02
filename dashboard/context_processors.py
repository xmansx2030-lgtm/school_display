from __future__ import annotations

from django.apps import apps
from django.urls import reverse

from core.models import SupportTicket
from core.system_access import NAV_PERMISSION_MAP, role_label

from .access import (
    get_system_permissions,
    get_system_role,
    has_system_permission,
    is_system_staff_user,
)


def _is_active_link(current_url_name: str, *, exact: tuple[str, ...] = (), startswith: tuple[str, ...] = ()) -> bool:
    name = (current_url_name or "").strip()
    if not name:
        return False
    if exact and name in exact:
        return True
    if startswith:
        for prefix in startswith:
            if name.startswith(prefix):
                return True
    return False


def _build_admin_nav_links(
    *,
    current_url_name: str,
    permission_keys: frozenset[str],
    is_superuser: bool,
    open_subscription_requests_count: int,
    open_support_tickets_count: int,
):
    """Single source of truth for SaaS admin navigation + dashboard action cards."""

    base_items = [
        {
            "key": "home",
            "group": "overview",
            "group_title": "نظرة عامة",
            "title": "الرئيسية",
            "description": "نظرة عامة على النظام والإحصائيات",
            "url_name": "dashboard:system_admin_dashboard",
            "icon": "fa-home",
            "emoji": "🏠",
            "tone": "slate",
            "exact": ("system_admin_dashboard",),
            "startswith": (),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "schools",
            "group": "customers",
            "group_title": "المدارس والحسابات",
            "title": "إدارة المدارس",
            "description": "إضافة وتعديل وإدارة المدارس",
            "url_name": "dashboard:system_schools_list",
            "icon": "fa-building",
            "emoji": "🏫",
            "tone": "blue",
            "exact": (),
            "startswith": ("system_school",),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "users",
            "group": "customers",
            "group_title": "المدارس والحسابات",
            "title": "مدراء المدارس",
            "description": "إدارة حسابات مدراء المدارس وربطهم",
            "url_name": "dashboard:system_users_list",
            "icon": "fa-user-circle",
            "emoji": "👤",
            "tone": "purple",
            "exact": (),
            "startswith": ("system_user",),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "employees",
            "group": "customers",
            "group_title": "المدارس والحسابات",
            "title": "إدارة الموظفين",
            "description": "إدارة موظفي النظام وصلاحياتهم",
            "url_name": "dashboard:system_employees_list",
            "icon": "fa-user-tie",
            "emoji": "🧑‍💼",
            "tone": "sky",
            "exact": (),
            "startswith": ("system_employee",),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "subscriptions",
            "group": "billing",
            "group_title": "الاشتراكات والفوترة",
            "title": "إدارة الاشتراكات",
            "description": "متابعة الاشتراكات والخطط النشطة",
            "url_name": "dashboard:system_subscriptions_list",
            "icon": "fa-file-invoice-dollar",
            "emoji": "💳",
            "tone": "emerald",
            "exact": (),
            "startswith": ("system_subscription",),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "subscription_requests",
            "group": "billing",
            "group_title": "الاشتراكات والفوترة",
            "title": "طلبات التجديد/الاشتراك",
            "description": "استعراض الطلبات واعتمادها أو رفضها",
            "url_name": "dashboard:system_subscription_requests_list",
            "icon": "fa-receipt",
            "emoji": "🧾",
            "tone": "rose",
            "exact": (),
            "startswith": ("system_subscription_request",),
            "visible": True,
            "badge_count": int(open_subscription_requests_count or 0),
        },
        {
            "key": "plans",
            "group": "billing",
            "group_title": "الاشتراكات والفوترة",
            "title": "إدارة الباقات والأسعار",
            "description": "إدارة الأسعار وحدود المدارس والشاشات",
            "url_name": "dashboard:system_plans_list",
            "icon": "fa-tags",
            "emoji": "🏷️",
            "tone": "indigo",
            "exact": (),
            "startswith": ("system_plan",),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "screen_addons",
            "group": "billing",
            "group_title": "الاشتراكات والفوترة",
            "title": "الشاشات الإضافية",
            "description": "إدارة زيادات الشاشات وتسعيرها",
            "url_name": "dashboard:system_screen_addons_list",
            "icon": "fa-display",
            "emoji": "🖥️",
            "tone": "sky",
            "exact": (),
            "startswith": ("system_screen_addon",),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "reports",
            "group": "operations",
            "group_title": "المتابعة والتشغيل",
            "title": "التقارير والإحصائيات",
            "description": "تقارير الأداء والإيرادات والنمو",
            "url_name": "dashboard:system_reports",
            "icon": "fa-chart-line",
            "emoji": "📊",
            "tone": "amber",
            "exact": ("system_reports",),
            "startswith": (),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "emergency_alerts",
            "group": "operations",
            "group_title": "المتابعة والتشغيل",
            "title": "التنبيهات الطارئة",
            "description": "إرسال تنبيه شامل لمدرسة أو عدة مدارس",
            "url_name": "dashboard:emergency_alert_list",
            "icon": "fa-triangle-exclamation",
            "emoji": "🚨",
            "tone": "rose",
            "exact": (),
            "startswith": ("emergency_alert",),
            "visible": True,
            "badge_count": 0,
        },
        {
            "key": "support",
            "group": "operations",
            "group_title": "المتابعة والتشغيل",
            "title": "تذاكر الدعم",
            "description": "متابعة طلبات الدعم الفني",
            "url_name": "dashboard:system_support_tickets",
            "icon": "fa-headset",
            "emoji": "🛟",
            "tone": "rose",
            "exact": (),
            "startswith": ("system_support",),
            "visible": True,
            "badge_count": int(open_support_tickets_count or 0),
        },
    ]

    links = []
    for item in base_items:
        if not item.get("visible", True):
            continue
        required_permission = NAV_PERMISSION_MAP.get(item["key"])
        if required_permission == "employees.owner_only" and not is_superuser:
            continue
        if (
            required_permission
            and required_permission != "employees.owner_only"
            and not is_superuser
            and required_permission not in permission_keys
        ):
            continue

        resolved = {
            "key": item["key"],
            "group": item.get("group", "other"),
            "group_title": item.get("group_title", "أخرى"),
            "title": item["title"],
            "description": item["description"],
            "icon": item["icon"],
            "emoji": item["emoji"],
            "tone": item["tone"],
            "url": reverse(item["url_name"]),
            "active": _is_active_link(
                current_url_name,
                exact=tuple(item.get("exact", ()) or ()),
                startswith=tuple(item.get("startswith", ()) or ()),
            ),
            "badge_count": int(item.get("badge_count") or 0),
        }

        if resolved["key"] == "subscriptions":
            if (current_url_name or "").startswith("system_subscription_request"):
                resolved["active"] = False

        links.append(resolved)

    return links


def admin_support_ticket_badges(request):
    """Global template context: admin-only support ticket badges.

    "New" tickets are interpreted as tickets with status == "open".
    """

    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return {}

    is_superuser = bool(getattr(user, "is_superuser", False))
    is_system_staff = is_system_staff_user(user)
    if not is_system_staff:
        return {}

    permission_keys = get_system_permissions(user)
    open_support_tickets_count = (
        SupportTicket.objects.filter(status="open").count()
        if has_system_permission(user, "support.view")
        else 0
    )
    counts = {
        "is_system_staff": True,
        # Backward-compatible template name: now means any delegated employee.
        "is_support_staff": bool(not is_superuser),
        "is_platform_employee": bool(not is_superuser),
        "is_superuser": is_superuser,
        "platform_role_label": "مالك المنصة" if is_superuser else role_label(get_system_role(user)),
        "admin_new_support_tickets_count": open_support_tickets_count,
    }

    for permission_key in (
        "schools.view",
        "schools.manage",
        "users.view",
        "users.manage",
        "subscriptions.view",
        "subscriptions.manage",
        "subscription_requests.view",
        "subscription_requests.manage",
        "plans.view",
        "plans.manage",
        "screen_addons.view",
        "screen_addons.manage",
        "reports.view",
        "support.view",
        "support.manage",
        "emergency_alerts.view",
        "emergency_alerts.manage",
    ):
        template_key = "can_" + permission_key.replace(".", "_")
        counts[template_key] = has_system_permission(user, permission_key)

    # Subscription requests (best-effort: feature may not exist in older DBs)
    open_subscription_requests_count = 0
    if has_system_permission(user, "subscription_requests.view"):
        try:
            Req = apps.get_model("subscriptions", "SubscriptionRequest")
            open_subscription_requests_count = Req.objects.filter(
                status__in=["submitted", "under_review"]
            ).count()
        except Exception:
            open_subscription_requests_count = 0

    counts["admin_open_subscription_requests_count"] = int(open_subscription_requests_count or 0)

    current_url_name = getattr(getattr(request, "resolver_match", None), "url_name", "") or ""
    counts["admin_nav_links"] = _build_admin_nav_links(
        current_url_name=current_url_name,
        permission_keys=permission_keys,
        is_superuser=is_superuser,
        open_subscription_requests_count=int(open_subscription_requests_count or 0),
        open_support_tickets_count=int(open_support_tickets_count or 0),
    )

    return counts

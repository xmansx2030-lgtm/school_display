from __future__ import annotations

from django.shortcuts import render
from django.urls import reverse


def permission_denied(request, exception=None):
    """Render a branded Arabic permission page for genuine 403 responses."""
    user = getattr(request, "user", None)
    is_authenticated = bool(user and getattr(user, "is_authenticated", False))

    is_system_staff = False
    if is_authenticated:
        try:
            has_employee_profile = bool(getattr(user, "is_staff", False) and user.system_employee_profile)
        except Exception:
            has_employee_profile = False
        try:
            is_legacy_support = user.groups.filter(name="Support").exists()
        except Exception:
            is_legacy_support = False
        is_system_staff = bool(
            getattr(user, "is_superuser", False) or has_employee_profile or is_legacy_support
        )

    if is_system_staff:
        return_url = reverse("dashboard:system_admin_dashboard")
        return_label = "العودة إلى لوحة إدارة النظام"
    elif is_authenticated:
        return_url = reverse("dashboard:index")
        return_label = "العودة إلى لوحة المدرسة"
    else:
        return_url = reverse("dashboard:login")
        return_label = "تسجيل الدخول"

    return render(
        request,
        "errors/403.html",
        {
            "return_url": return_url,
            "return_label": return_label,
            "is_authenticated": is_authenticated,
        },
        status=403,
    )

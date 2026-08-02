from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from core.tenant_access import authorized_active_school

from .access import (
    get_or_create_user_profile,
    has_system_permission,
    is_system_staff_user,
)


def manager_required(view_func: Callable[..., Any]):
    """
    يمرر:
    - superuser
    - أو أي مستخدم مرتبط بمدرسة عبر UserProfile (schools/active_school)

    إذا ما عنده مدرسة: نوجهه لصفحة اختيار المدرسة بدل 403
    """
    @login_required(login_url="dashboard:login")
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        user = request.user

        if getattr(user, "is_superuser", False):
            return view_func(request, *args, **kwargs)

        # Support accounts are system-level identities and must never cross
        # into school-manager endpoints, even if a stale profile is attached.
        if is_system_staff_user(user):
            raise PermissionDenied("حساب موظف المنصة غير مخول بالدخول إلى لوحة مدرسة.")

        profile = get_or_create_user_profile(user)

        # عنده active_school ومصرح بها
        if authorized_active_school(profile, user=user) is not None:
            return view_func(request, *args, **kwargs)

        # عنده مدارس لكن ما اختار active_school
        try:
            if profile.schools.exists():
                messages.info(request, "فضلاً اختر المدرسة النشطة أولاً.")
                return redirect("dashboard:select_school")
        except Exception:
            pass

        # ما عنده مدارس إطلاقًا
        raise PermissionDenied("حسابك غير مرتبط بأي مدرسة للوصول إلى لوحة التحكم.")

    return _wrapped


def superuser_required(view_func: Callable[..., Any]):
    """
    صفحات لوحة إدارة النظام (SaaS) للسوبر فقط.
    """
    @login_required(login_url="dashboard:login")
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if getattr(request.user, "is_superuser", False):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("هذه الصفحة مخصصة لمدير النظام فقط.")
    return _wrapped


def system_staff_required(view_func: Callable[..., Any]):
    """Allow authenticated platform employees and the platform owner."""

    @login_required(login_url="dashboard:login")
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if is_system_staff_user(request.user):
            return view_func(request, *args, **kwargs)
        raise PermissionDenied("هذه الصفحة مخصصة لفريق إدارة النظام فقط.")

    return _wrapped


def system_permission_required(permission_key: str):
    """Protect a platform view with one explicit server-side permission."""

    def decorator(view_func: Callable[..., Any]):
        @login_required(login_url="dashboard:login")
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if has_system_permission(request.user, permission_key):
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("هذه العملية ليست ضمن صلاحيات موظف المنصة.")

        return _wrapped

    return decorator


def manager_or_system_permission_required(permission_key: str):
    """Allow a school manager normally, or a platform employee with permission."""

    def decorator(view_func: Callable[..., Any]):
        manager_view = manager_required(view_func)

        @login_required(login_url="dashboard:login")
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if is_system_staff_user(request.user):
                if has_system_permission(request.user, permission_key):
                    return view_func(request, *args, **kwargs)
                raise PermissionDenied("هذه العملية ليست ضمن صلاحيات موظف المنصة.")
            return manager_view(request, *args, **kwargs)

        return _wrapped

    return decorator

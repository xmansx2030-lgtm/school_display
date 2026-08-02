from __future__ import annotations

import logging

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from core.models import School, UserProfile
from core.system_access import PERMISSION_KEYS, ROLE_SUPPORT, normalize_permission_keys, role_permissions
from core.tenant_access import authorized_active_school


logger = logging.getLogger(__name__)


def is_support_user(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    try:
        employee = user.system_employee_profile
        if employee.role == ROLE_SUPPORT:
            return True
    except ObjectDoesNotExist:
        pass
    except Exception:
        logger.exception("employee_role_check_failed user_id=%s", getattr(user, "pk", None))
    try:
        return user.groups.filter(name="Support").exists()
    except Exception:
        logger.exception("support_membership_check_failed user_id=%s", getattr(user, "pk", None))
        return False


def is_system_staff_user(user) -> bool:
    if not bool(getattr(user, "is_authenticated", False)) or not bool(getattr(user, "is_active", False)):
        return False
    if bool(getattr(user, "is_superuser", False)):
        return True
    try:
        has_employee_profile = bool(user.system_employee_profile)
    except ObjectDoesNotExist:
        has_employee_profile = False
    except Exception:
        logger.exception("employee_profile_check_failed user_id=%s", getattr(user, "pk", None))
        has_employee_profile = False
    return bool(getattr(user, "is_staff", False) and has_employee_profile) or is_support_user(user)


def get_system_permissions(user) -> frozenset[str]:
    """Resolve platform permissions, retaining safe support-role compatibility."""

    if not is_system_staff_user(user):
        return frozenset()
    if bool(getattr(user, "is_superuser", False)):
        return frozenset(PERMISSION_KEYS)

    cached = getattr(user, "_system_permission_keys_cache", None)
    if cached is not None:
        return cached

    try:
        employee = user.system_employee_profile
        resolved = frozenset(normalize_permission_keys(employee.permission_keys))
    except ObjectDoesNotExist:
        resolved = frozenset(role_permissions(ROLE_SUPPORT)) if is_support_user(user) else frozenset()
    except Exception:
        logger.exception("employee_permissions_check_failed user_id=%s", getattr(user, "pk", None))
        resolved = frozenset()
    user._system_permission_keys_cache = resolved
    return resolved


def has_system_permission(user, permission_key: str) -> bool:
    if bool(getattr(user, "is_superuser", False)) and bool(getattr(user, "is_authenticated", False)):
        return True
    return str(permission_key) in get_system_permissions(user)


def get_system_role(user) -> str:
    if bool(getattr(user, "is_superuser", False)):
        return "owner"
    try:
        return str(user.system_employee_profile.role)
    except ObjectDoesNotExist:
        return ROLE_SUPPORT if is_support_user(user) else ""


def get_or_create_user_profile(user) -> UserProfile:
    """Return the user's tenant profile and synchronize superuser school access."""
    profile, _created = UserProfile.objects.get_or_create(user=user)

    if not getattr(user, "is_superuser", False):
        return profile

    all_school_ids = list(School.objects.order_by("id").values_list("id", flat=True))
    current_school_ids = set(profile.schools.values_list("id", flat=True))
    missing_school_ids = [school_id for school_id in all_school_ids if school_id not in current_school_ids]
    if missing_school_ids:
        profile.schools.add(*School.objects.filter(id__in=missing_school_ids))

    if all_school_ids and profile.active_school_id not in all_school_ids:
        profile.active_school_id = all_school_ids[0]
        profile.save(update_fields=["active_school"])
    elif not all_school_ids and profile.active_school_id is not None:
        profile.active_school = None
        profile.save(update_fields=["active_school"])
    return profile


def safe_next_url(request, default_name: str = "dashboard:index") -> str:
    candidate = (request.GET.get("next") or request.POST.get("next") or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse(default_name)


def active_school_for_user(user, *, auto_select: bool = True):
    """Return only an authorized school and optionally select the first tenant."""
    profile = get_or_create_user_profile(user)
    school = authorized_active_school(profile, user=user)
    if school is not None or not auto_select:
        return school

    school = profile.schools.order_by("id").first()
    if school is not None:
        profile.active_school = school
        profile.save(update_fields=["active_school"])
    return school


def get_active_school_or_redirect(request):
    """Resolve the authorized tenant, returning ``(school, redirect_response)``."""
    profile = get_or_create_user_profile(request.user)
    active_school = authorized_active_school(profile, user=request.user)
    if active_school is not None:
        request.school = active_school
        return active_school, None

    first_school = profile.schools.order_by("id").first()
    if first_school is not None:
        profile.active_school = first_school
        profile.save(update_fields=["active_school"])
        request.school = first_school
        messages.info(request, f"تم تعيين المدرسة النشطة تلقائيًا: {first_school.name}")
        return first_school, None

    request.school = None
    if is_system_staff_user(request.user):
        messages.info(request, "لا توجد مدرسة مرتبطة بحسابك — تم تحويلك للوحة إدارة النظام.")
        return None, redirect("dashboard:system_admin_dashboard")

    messages.error(request, "حسابك غير مرتبط بأي مدرسة. يرجى التواصل مع الإدارة.")
    return None, redirect("dashboard:select_school")

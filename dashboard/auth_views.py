from __future__ import annotations

import hashlib
import logging
import re

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm, SetPasswordForm
from django.contrib.auth.views import (
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django import forms
from django.middleware.csrf import get_token
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie

from core.login_security import clear_login_rate_limit, login_rate_limit_status, record_failed_login
from core.two_factor import begin_challenge, get_enabled_config, two_factor_required_for

from .access import get_active_school_or_redirect, is_system_staff_user, safe_next_url
from .decorators import manager_required


logger = logging.getLogger(__name__)
UserModel = get_user_model()
_ARABIC_DIGITS_TRANS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


class ArabicPasswordResetForm(PasswordResetForm):
    email = forms.EmailField(
        label="البريد الإلكتروني المسجل",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "autocomplete": "email",
                "placeholder": "name@example.com",
                "autofocus": True,
            }
        ),
    )


class ArabicSetPasswordForm(SetPasswordForm):
    new_password1 = forms.CharField(
        label="كلمة المرور الجديدة",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    new_password2 = forms.CharField(
        label="تأكيد كلمة المرور الجديدة",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )


class DashboardPasswordResetView(PasswordResetView):
    template_name = "dashboard/password_reset_form.html"
    form_class = ArabicPasswordResetForm
    email_template_name = "dashboard/password_reset_email.txt"
    html_email_template_name = "dashboard/password_reset_email.html"
    subject_template_name = "dashboard/password_reset_subject.txt"
    success_url = reverse_lazy("dashboard:password_reset_done")


class DashboardPasswordResetDoneView(PasswordResetDoneView):
    template_name = "dashboard/password_reset_done.html"


class DashboardPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "dashboard/password_reset_confirm.html"
    form_class = ArabicSetPasswordForm
    success_url = reverse_lazy("dashboard:password_reset_complete")


class DashboardPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "dashboard/password_reset_complete.html"


def _normalize_login_mobile(value: str) -> str:
    raw = (value or "").translate(_ARABIC_DIGITS_TRANS)
    digits = re.sub(r"\D+", "", raw)
    if digits.startswith("00966"):
        digits = "0" + digits[5:]
    elif digits.startswith("966"):
        digits = "0" + digits[3:]
    return digits


def _resolve_login_identifier(identifier: str) -> str:
    """Allow users to sign in with username or the mobile saved on their profile."""
    identifier = (identifier or "").strip()
    mobile = _normalize_login_mobile(identifier)
    if re.fullmatch(r"05\d{8}", mobile or ""):
        try:
            user = UserModel.objects.filter(profile__mobile=mobile).first()
        except Exception:
            user = None
        if user is not None:
            return getattr(user, "username", identifier)
    return identifier


def _needs_onboarding(user) -> bool:
    try:
        return bool(user.profile.needs_onboarding)
    except Exception:
        return False


def _has_requested_next(request) -> bool:
    return bool((request.GET.get("next") or request.POST.get("next") or "").strip())


@never_cache
@ensure_csrf_cookie
@csrf_protect
def login_view(request):
    next_url = safe_next_url(request, default_name="dashboard:index")

    if request.user.is_authenticated:
        if is_system_staff_user(request.user):
            return redirect("dashboard:system_admin_dashboard")
        _school, response = get_active_school_or_redirect(request)
        if response:
            return response
        if _has_requested_next(request):
            return redirect(next_url)
        if _needs_onboarding(request.user):
            return redirect("dashboard:help_getting_started")
        return redirect("dashboard:index")

    if request.method == "POST":
        identifier = (request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        next_url = safe_next_url(request, default_name="dashboard:index")
        normalized_mobile = _normalize_login_mobile(identifier)
        rate_identifier = normalized_mobile if re.fullmatch(r"05\d{8}", normalized_mobile or "") else identifier
        rate_status = login_rate_limit_status(request, rate_identifier)
        if rate_status.blocked:
            logger.warning(
                "login_rate_limited identifier_hash=%s path=%s",
                hashlib.sha256(rate_identifier.casefold().encode("utf-8", errors="ignore")).hexdigest()[:16],
                getattr(request, "path", ""),
            )
            messages.error(request, "تم إيقاف محاولات الدخول مؤقتًا لكثرة المحاولات. حاول لاحقًا.")
            response = render(request, "dashboard/login.html", {"next": next_url}, status=429)
            response["Retry-After"] = str(rate_status.retry_after_seconds)
            return response

        auth_username = _resolve_login_identifier(identifier)
        user = authenticate(request, username=auth_username, password=password)
        if user:
            clear_login_rate_limit(request, rate_identifier)
            if get_enabled_config(user) is not None:
                begin_challenge(
                    request,
                    user,
                    backend=getattr(user, "backend", "django.contrib.auth.backends.ModelBackend"),
                    next_url=next_url,
                )
                return redirect("dashboard:two_factor_verify")

            login(request, user)
            if is_system_staff_user(user):
                if two_factor_required_for(user) and get_enabled_config(user) is None:
                    return redirect("dashboard:two_factor_setup")
                return redirect(safe_next_url(request, default_name="dashboard:system_admin_dashboard"))
            _school, response = get_active_school_or_redirect(request)
            if response:
                return response
            if _has_requested_next(request):
                return redirect(next_url)
            if _needs_onboarding(user):
                return redirect("dashboard:help_getting_started")
            return redirect(safe_next_url(request, default_name="dashboard:index"))

        record_failed_login(request, rate_identifier)
        logger.warning(
            "login_failed identifier_hash=%s next=%s path=%s",
            hashlib.sha256(rate_identifier.casefold().encode("utf-8", errors="ignore")).hexdigest()[:16],
            next_url,
            getattr(request, "path", ""),
        )
        messages.error(request, "بيانات الدخول غير صحيحة.")

    get_token(request)
    return render(request, "dashboard/login.html", {"next": next_url})


def logout_view(request):
    logout(request)
    return redirect("dashboard:login")


@manager_required
def change_password(request):
    next_url = safe_next_url(request, default_name="dashboard:index")
    if request.method == "POST":
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "تم تغيير كلمة المرور بنجاح!")
            return redirect(next_url)
        messages.error(request, "الرجاء تصحيح الأخطاء أدناه.")
    else:
        form = PasswordChangeForm(request.user)
    return render(request, "dashboard/change_password.html", {"form": form, "next": next_url})

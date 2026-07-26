from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from core.two_factor import (
    PENDING_ATTEMPTS_KEY,
    challenge_payload,
    clear_challenge,
    consume_second_factor,
    decrypt_secret,
    enable_two_factor,
    ensure_setup_config,
    get_enabled_config,
    provisioning_uri,
    qr_code_data_uri,
    reset_two_factor,
    two_factor_required_for,
    verify_setup_token,
)


UserModel = get_user_model()


def _safe_pending_next(request, value: str) -> str:
    fallback = reverse("dashboard:system_admin_dashboard")
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return fallback


@never_cache
@csrf_protect
def two_factor_verify(request):
    payload = challenge_payload(request)
    if payload is None:
        messages.info(request, "انتهت جلسة التحقق. سجّل الدخول مرة أخرى.")
        return redirect("dashboard:login")

    try:
        user = UserModel.objects.get(pk=payload["user_id"], is_active=True)
    except UserModel.DoesNotExist:
        clear_challenge(request)
        raise Http404

    if get_enabled_config(user) is None:
        clear_challenge(request)
        return redirect("dashboard:login")

    if request.method == "POST":
        token = (request.POST.get("token") or "").strip()
        if consume_second_factor(user, token):
            backend = payload["backend"]
            next_url = _safe_pending_next(request, payload["next_url"])
            clear_challenge(request)
            login(request, user, backend=backend)
            return redirect(next_url)

        attempts = int(payload["attempts"]) + 1
        request.session[PENDING_ATTEMPTS_KEY] = attempts
        max_attempts = int(getattr(settings, "TWO_FACTOR_MAX_ATTEMPTS", 8) or 8)
        if attempts >= max_attempts:
            clear_challenge(request)
            messages.error(request, "تم إلغاء جلسة التحقق لكثرة المحاولات غير الصحيحة.")
            return redirect("dashboard:login")
        messages.error(request, "رمز التحقق أو رمز الاسترداد غير صحيح.")

    return render(
        request,
        "dashboard/two_factor_verify.html",
        {"username": getattr(user, "username", ""), "remaining_attempts": max(0, int(getattr(settings, "TWO_FACTOR_MAX_ATTEMPTS", 8)) - int(request.session.get(PENDING_ATTEMPTS_KEY, 0)))},
    )


@login_required(login_url="dashboard:login")
@never_cache
@csrf_protect
def two_factor_setup(request):
    existing = get_enabled_config(request.user)
    if existing is not None:
        return render(
            request,
            "dashboard/two_factor_enabled.html",
            {"can_disable": not two_factor_required_for(request.user)},
        )

    config = ensure_setup_config(request.user)
    if request.method == "POST":
        token = (request.POST.get("token") or "").strip()
        if verify_setup_token(config, token):
            recovery_codes = enable_two_factor(config)
            return render(
                request,
                "dashboard/two_factor_recovery_codes.html",
                {"recovery_codes": recovery_codes},
            )
        messages.error(request, "الرمز غير صحيح. تأكد من ضبط وقت الهاتف تلقائيًا ثم حاول مجددًا.")

    uri = provisioning_uri(config)
    secret = decrypt_secret(config)
    grouped_secret = " ".join(secret[index:index + 4] for index in range(0, len(secret), 4))
    return render(
        request,
        "dashboard/two_factor_setup.html",
        {"qr_code_data_uri": qr_code_data_uri(uri), "manual_secret": grouped_secret},
    )


@login_required(login_url="dashboard:login")
@never_cache
@csrf_protect
def two_factor_disable(request):
    if get_enabled_config(request.user) is None:
        messages.info(request, "المصادقة الثنائية غير مفعلة لهذا الحساب.")
        return redirect("dashboard:two_factor_setup")

    if two_factor_required_for(request.user):
        messages.error(
            request,
            "لا يمكن إلغاء المصادقة الثنائية لأن سياسة النظام تفرضها على هذا الحساب.",
        )
        return redirect("dashboard:two_factor_setup")

    if request.method == "POST":
        password = request.POST.get("password") or ""
        token = (request.POST.get("token") or "").strip()

        if not request.user.check_password(password):
            messages.error(request, "كلمة المرور الحالية غير صحيحة.")
        elif not consume_second_factor(request.user, token):
            messages.error(request, "رمز المصادقة أو رمز الاسترداد غير صحيح.")
        else:
            reset_two_factor(request.user)
            logout(request)
            messages.success(
                request,
                "تم إلغاء المصادقة الثنائية. يمكنك تفعيلها مجددًا من إعدادات الأمان.",
            )
            return redirect("dashboard:login")

    return render(request, "dashboard/two_factor_disable.html")

from __future__ import annotations

from datetime import time, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import resolve, reverse
from django.utils import timezone
import pyotp

from core.models import (
    DisplayScreen,
    School,
    SubscriptionPlan,
    SupportTicket,
    SystemEmployeeProfile,
    UserProfile,
    UserTwoFactorAuth,
)
from core.system_access import ROLE_FINANCE, ROLE_SUPPORT, role_permissions
from core.display_presence import latest_display_presence, touch_display_presence
from display.services.device_binding import bind_device_atomic
from display.ws_groups import school_group_name
from core.error_views import permission_denied
from core.tenant_access import authorized_active_school
from core.two_factor import (
    consume_second_factor,
    decrypt_secret,
    enable_two_factor,
    ensure_setup_config,
)
from dashboard.access import get_active_school_or_redirect
from dashboard.decorators import manager_required, superuser_required, system_staff_required
from dashboard.forms import SubscriptionNewRequestForm
from subscriptions.models import SchoolSubscription, SubscriptionRequest, TamaraCheckout
from schedule.models import ClassLesson, DaySchedule, Period, SchoolClass, SchoolSettings, Subject, Teacher
from notices.models import Announcement, EmergencyAlert
from dashboard.excel_import import apply_import, build_template_bytes, parse_workbook
from schedule.api_views import _merge_real_data_into_snapshot


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "dashboard-security-tests",
    }
}


@override_settings(
    CACHES=TEST_CACHES,
    LOGIN_RATE_LIMIT_WINDOW_SECONDS=120,
    LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS=2,
    LOGIN_RATE_LIMIT_IP_ATTEMPTS=20,
)
class LoginRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(
            username="school_manager",
            password="CorrectPass123!",
        )

    def test_repeated_failed_logins_are_temporarily_blocked(self):
        url = reverse("dashboard:login")
        for _ in range(2):
            response = self.client.post(
                url,
                {"username": self.user.username, "password": "wrong"},
                REMOTE_ADDR="203.0.113.10",
            )
            self.assertEqual(response.status_code, 200)

        blocked = self.client.post(
            url,
            {"username": self.user.username, "password": "wrong"},
            REMOTE_ADDR="203.0.113.10",
        )

        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked["Retry-After"], "120")
        self.assertContains(blocked, "تم إيقاف محاولات الدخول مؤقتًا", status_code=429)

    def test_successful_login_clears_the_account_failure_counter(self):
        url = reverse("dashboard:login")
        self.client.post(
            url,
            {"username": self.user.username, "password": "wrong"},
            REMOTE_ADDR="203.0.113.11",
        )
        success = self.client.post(
            url,
            {"username": self.user.username, "password": "CorrectPass123!"},
            REMOTE_ADDR="203.0.113.11",
        )

        self.assertEqual(success.status_code, 302)

        self.client.logout()
        after_success = self.client.post(
            url,
            {"username": self.user.username, "password": "wrong"},
            REMOTE_ADDR="203.0.113.11",
        )
        self.assertEqual(after_success.status_code, 200)

    def test_login_template_keeps_desktop_tablet_mobile_layout_contract(self):
        response = self.client.get(reverse("dashboard:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "width: min(640px, 100%)")
        self.assertContains(response, "@media (min-width: 768px) and (max-width: 1179px)")
        self.assertContains(response, "@media (max-width: 767px)")
        self.assertContains(response, 'for="loginUsername"')
        self.assertContains(response, 'for="loginPassword"')
        self.assertNotContains(response, "Smart School Control")


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
    PASSWORD_RESET_TIMEOUT=3600,
)
class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="reset_manager",
            email="reset@example.com",
            password="OldStrongPass123!",
        )

    def test_login_page_links_to_password_reset(self):
        response = self.client.get(reverse("dashboard:login"))

        self.assertContains(response, reverse("dashboard:password_reset"))
        self.assertContains(response, "نسيت كلمة المرور؟")

    def test_password_reset_email_changes_password(self):
        response = self.client.post(
            reverse("dashboard:password_reset"),
            {"email": "reset@example.com"},
        )

        self.assertRedirects(
            response,
            reverse("dashboard:password_reset_done"),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["reset@example.com"])
        self.assertIn("استعادة كلمة المرور", mail.outbox[0].subject)

        reset_url = next(
            line.strip()
            for line in mail.outbox[0].body.splitlines()
            if "/dashboard/reset/" in line
        )
        token_response = self.client.get(reset_url)
        self.assertEqual(token_response.status_code, 302)

        set_password_response = self.client.post(
            token_response.url,
            {
                "new_password1": "NewStrongPass456!",
                "new_password2": "NewStrongPass456!",
            },
        )
        self.assertRedirects(
            set_password_response,
            reverse("dashboard:password_reset_complete"),
            fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrongPass456!"))

    def test_unknown_email_uses_same_success_page_without_sending(self):
        response = self.client.post(
            reverse("dashboard:password_reset"),
            {"email": "unknown@example.com"},
        )

        self.assertRedirects(
            response,
            reverse("dashboard:password_reset_done"),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 0)


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.allowed_school = School.objects.create(name="المدرسة المصرح بها", slug="allowed-school")
        self.other_school = School.objects.create(name="مدرسة أخرى", slug="other-school")
        self.user = get_user_model().objects.create_user(
            username="tenant_manager",
            password="StrongPass123!",
        )
        self.profile = UserProfile.objects.create(user=self.user, active_school=self.other_school)
        self.profile.schools.add(self.allowed_school)

    def test_invalid_active_school_is_cleared(self):
        school = authorized_active_school(self.profile, user=self.user)

        self.assertIsNone(school)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.active_school)

    def test_dashboard_never_uses_a_school_outside_profile_membership(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("dashboard:index"))

        self.assertRedirects(response, reverse("dashboard:select_school"), fetch_redirect_response=False)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.active_school)


@override_settings(
    CACHES=TEST_CACHES,
    TWO_FACTOR_REQUIRED_FOR_PRIVILEGED=True,
    TWO_FACTOR_ENCRYPTION_KEY="",
)
class PrivilegedTwoFactorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_superuser(
            username="system_admin",
            email="admin@example.com",
            password="StrongPass123!",
        )

    def _enable(self):
        config = ensure_setup_config(self.user)
        codes = enable_two_factor(config)
        return config, codes

    def test_privileged_login_requires_initial_enrollment(self):
        response = self.client.post(
            reverse("dashboard:login"),
            {"username": self.user.username, "password": "StrongPass123!"},
        )

        self.assertRedirects(response, reverse("dashboard:two_factor_setup"), fetch_redirect_response=False)

    def test_setup_enables_totp_and_displays_one_time_recovery_codes(self):
        self.client.force_login(self.user)
        config = ensure_setup_config(self.user)
        token = pyotp.TOTP(decrypt_secret(config)).now()

        response = self.client.post(reverse("dashboard:two_factor_setup"), {"token": token})

        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertTrue(config.is_enabled)
        self.assertEqual(len(config.recovery_code_hashes), 10)
        self.assertContains(response, "لن تظهر مرة أخرى")

    def test_enabled_totp_is_required_before_creating_authenticated_session(self):
        config, _codes = self._enable()
        original_session = self.client.session
        original_session.save()
        original_session_key = original_session.session_key

        password_step = self.client.post(
            reverse("dashboard:login"),
            {"username": self.user.username, "password": "StrongPass123!"},
        )

        self.assertRedirects(password_step, reverse("dashboard:two_factor_verify"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotEqual(self.client.session.session_key, original_session_key)

        token = pyotp.TOTP(decrypt_secret(config)).now()
        verify_step = self.client.post(reverse("dashboard:two_factor_verify"), {"token": token})

        self.assertEqual(verify_step.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_totp_cannot_be_replayed_within_the_same_time_window(self):
        config, _codes = self._enable()
        token = pyotp.TOTP(decrypt_secret(config)).now()

        self.assertTrue(consume_second_factor(self.user, token))
        self.assertFalse(consume_second_factor(self.user, token))

    def test_recovery_code_is_single_use(self):
        _config, codes = self._enable()

        self.assertTrue(consume_second_factor(self.user, codes[0]))
        self.assertFalse(consume_second_factor(self.user, codes[0]))

    def test_mandatory_privileged_two_factor_cannot_be_disabled(self):
        config, codes = self._enable()
        self.client.force_login(self.user)

        setup_response = self.client.get(reverse("dashboard:two_factor_setup"))
        response = self.client.post(
            reverse("dashboard:two_factor_disable"),
            {"password": "StrongPass123!", "token": codes[0]},
        )

        self.assertContains(setup_response, "المصادقة الثنائية إلزامية")
        self.assertNotContains(setup_response, "إلغاء المصادقة الثنائية")
        self.assertRedirects(response, reverse("dashboard:two_factor_setup"), fetch_redirect_response=False)
        config.refresh_from_db()
        self.assertTrue(config.is_enabled)


@override_settings(
    CACHES=TEST_CACHES,
    TWO_FACTOR_REQUIRED_FOR_PRIVILEGED=True,
    TWO_FACTOR_ENCRYPTION_KEY="",
)
class SchoolManagerTwoFactorTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة المدير", slug="manager-2fa-school")
        self.user = get_user_model().objects.create_user(
            username="manager_2fa",
            password="StrongPass123!",
        )
        profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        profile.schools.add(self.school)
        plan = SubscriptionPlan.objects.create(
            code="manager-2fa-plan",
            name="خطة المدير",
            price=100,
            duration_days=30,
            max_screens=1,
        )
        SchoolSubscription.objects.create(
            school=self.school,
            plan=plan,
            starts_at=timezone.localdate(),
            status="active",
        )

    def test_manager_can_open_two_factor_setup_from_security_settings(self):
        self.client.force_login(self.user)

        setup_response = self.client.get(reverse("dashboard:two_factor_setup"))
        settings_response = self.client.get(reverse("dashboard:settings"))

        self.assertEqual(setup_response.status_code, 200)
        self.assertContains(settings_response, "تفعيل المصادقة الثنائية")

    def test_manager_with_enabled_two_factor_is_challenged_at_login(self):
        config = ensure_setup_config(self.user)
        enable_two_factor(config)

        response = self.client.post(
            reverse("dashboard:login"),
            {"username": self.user.username, "password": "StrongPass123!"},
        )

        self.assertRedirects(response, reverse("dashboard:two_factor_verify"), fetch_redirect_response=False)

    def test_manager_can_disable_two_factor_with_password_and_current_token(self):
        config = ensure_setup_config(self.user)
        enable_two_factor(config)
        token = pyotp.TOTP(decrypt_secret(config)).now()
        self.client.force_login(self.user)

        setup_response = self.client.get(reverse("dashboard:two_factor_setup"))
        response = self.client.post(
            reverse("dashboard:two_factor_disable"),
            {"password": "StrongPass123!", "token": token},
        )

        self.assertContains(setup_response, "إلغاء المصادقة الثنائية")
        self.assertRedirects(response, reverse("dashboard:login"), fetch_redirect_response=False)
        self.assertFalse(UserTwoFactorAuth.objects.filter(user=self.user).exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_manager_cannot_disable_two_factor_with_wrong_password(self):
        config = ensure_setup_config(self.user)
        enable_two_factor(config)
        token = pyotp.TOTP(decrypt_secret(config)).now()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dashboard:two_factor_disable"),
            {"password": "wrong", "token": token},
        )

        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertTrue(config.is_enabled)


class CustomerExperienceRegressionTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة التجربة", slug="customer-experience-school")
        self.user = get_user_model().objects.create_user(username="customer_manager", password="StrongPass123!")
        self.profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        self.profile.schools.add(self.school)
        self.settings = SchoolSettings.objects.create(name=self.school.name, school=self.school)
        plan = SubscriptionPlan.objects.create(
            code="customer-plan",
            name="الخطة السنوية",
            description="الخيار المناسب لتشغيل شاشات المدرسة",
            price=100,
            duration_days=365,
            max_screens=3,
            max_users=4,
            is_featured=True,
        )
        SchoolSubscription.objects.create(
            school=self.school,
            plan=plan,
            starts_at=timezone.localdate(),
            status="active",
            notes="internal mobile: 0500000000",
        )
        self.client.force_login(self.user)

    def test_subscription_uses_real_screen_connection_state_and_hides_internal_notes(self):
        DisplayScreen.objects.create(name="الشاشة الرئيسية", school=self.school, is_active=True)

        response = self.client.get(reverse("dashboard:my_subscription"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "بانتظار أول اتصال")
        self.assertNotContains(response, "جميع الشاشات تعمل")
        self.assertNotContains(response, "internal mobile")
        self.assertNotContains(response, "مدرسة مدرسة التجربة")
        self.assertContains(response, "data-plan-option")
        self.assertContains(response, 'id="plan-filter-search"')
        self.assertContains(response, 'id="plan-filter-duration"')
        self.assertContains(response, 'id="plan-filter-screens"')
        self.assertContains(response, 'data-payment-hub="new"')
        self.assertContains(response, 'data-payment-hub="renewal"')
        self.assertContains(response, 'data-payment-choice="new"', count=3)
        self.assertContains(response, 'data-payment-choice="renewal"', count=3)
        self.assertContains(response, "خطوة واحدة واضحة")
        self.assertContains(response, "الخيار المناسب لتشغيل شاشات المدرسة")
        self.assertContains(response, "الأكثر طلباً")
        self.assertNotContains(response, "المدارس:")

    def test_school_settings_exposes_clear_sections_and_display_preview(self):
        screen = DisplayScreen.objects.create(
            name="الشاشة الرئيسية",
            school=self.school,
            is_active=True,
        )

        response = self.client.get(reverse("dashboard:settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الهوية والمظهر")
        self.assertContains(response, "رسائل الشاشة")
        self.assertContains(response, "سرعة العرض")
        self.assertContains(response, "معاينة شاشة المدرسة")
        self.assertContains(response, f"/s/{screen.short_code}/")
        self.assertContains(response, "كل التغييرات محفوظة")

    @override_settings(
        TAMARA_ENABLED=True,
        TAMARA_API_TOKEN="test-token",
        TAMARA_API_BASE_URL="https://api-sandbox.tamara.co",
    )
    def test_subscription_page_reviews_tamara_payment_before_redirect(self):
        current_plan = SchoolSubscription.objects.get(school=self.school).plan
        TamaraCheckout.objects.create(
            school=self.school,
            created_by=self.user,
            plan=current_plan,
            request_type="new",
            amount=current_plan.price,
            status="error",
            error_message="تعذر الاتصال بتمارا حاليًا.",
        )

        response = self.client.get(reverse("dashboard:my_subscription"))

        self.assertContains(response, "مراجعة قبل الانتقال")
        self.assertContains(response, "لن يتم الخصم من هذه الصفحة")
        self.assertContains(response, "data-tamara-form")
        self.assertContains(response, "تعذر الاتصال بتمارا حاليًا.")
        self.assertContains(response, "إعادة المحاولة")
        self.assertContains(response, "التحويل البنكي بدلًا من ذلك")

    def test_free_plan_needs_no_receipt_but_paid_bank_transfer_does(self):
        free_plan = SubscriptionPlan.objects.create(
            code="customer-free-plan",
            name="تجربة مجانية",
            price=0,
            duration_days=14,
            max_screens=1,
        )
        paid_plan = SchoolSubscription.objects.get(school=self.school).plan

        free_form = SubscriptionNewRequestForm(
            data={"new-plan": free_plan.pk},
            prefix="new",
        )
        paid_form = SubscriptionNewRequestForm(
            data={"new-plan": paid_plan.pk},
            prefix="new",
        )

        self.assertTrue(free_form.is_valid(), free_form.errors)
        self.assertFalse(paid_form.is_valid())
        self.assertIn("receipt_image", paid_form.errors)

        response = self.client.post(
            reverse("dashboard:my_subscription"),
            {"action": "new", "new-plan": free_plan.pk},
        )
        self.assertRedirects(
            response,
            reverse("dashboard:my_subscription"),
            fetch_redirect_response=False,
        )
        request_obj = SubscriptionRequest.objects.get(plan=free_plan)
        self.assertFalse(bool(request_obj.receipt_image))

    def test_connected_screen_is_reported_live_in_screen_list(self):
        screen = DisplayScreen.objects.create(
            name="الشاشة الرئيسية",
            school=self.school,
            is_active=True,
            bound_device_id="tv-device-1",
        )
        touch_display_presence(screen.pk, token=screen.token)

        response = self.client.get(reverse("dashboard:screen_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "متصلة الآن")
        self.assertContains(response, "قبل أقل من دقيقة")
        self.assertNotContains(response, "لم تتصل بعد")

    def test_manager_can_customize_each_screen_independently(self):
        first = DisplayScreen.objects.create(name="شاشة المدخل", school=self.school)
        second = DisplayScreen.objects.create(name="شاشة المعلمين", school=self.school)

        form_response = self.client.get(reverse("dashboard:screen_edit", args=[first.pk]))
        self.assertEqual(form_response.status_code, 200)
        self.assertContains(form_response, "الثيم الخاص بالشاشة")
        self.assertContains(form_response, "قالب المناسبة")
        self.assertContains(form_response, "إظهار جدول الحصص الجارية")
        self.assertContains(form_response, "إظهار حصص الانتظار")
        self.assertContains(form_response, "إظهار الإشراف والمناوبة")
        self.assertContains(form_response, "إظهار لوحة الشرف")

        response = self.client.post(
            reverse("dashboard:screen_edit", args=[first.pk]),
            {
                "name": first.name,
                "is_active": "on",
                "theme_override": "rose",
                "occasion_theme": "graduation",
                "featured_panel_override": "duty",
                "show_announcements": "on",
                "show_period_classes": "on",
                "show_duty": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard:screen_list"),
            fetch_redirect_response=False,
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.theme_override, "rose")
        self.assertEqual(first.occasion_theme, "graduation")
        self.assertEqual(first.featured_panel_override, "duty")
        self.assertTrue(first.show_announcements)
        self.assertTrue(first.show_period_classes)
        self.assertFalse(first.show_standby)
        self.assertTrue(first.show_duty)
        self.assertFalse(first.show_excellence)
        self.assertEqual(second.theme_override, "")
        self.assertEqual(second.occasion_theme, "auto")
        self.assertTrue(second.show_standby)
        self.assertTrue(second.show_excellence)

    def test_screen_customization_is_scoped_to_active_school(self):
        other_school = School.objects.create(name="مدرسة أخرى", slug="other-screen-school")
        other_screen = DisplayScreen.objects.create(name="شاشة أخرى", school=other_school)

        response = self.client.get(reverse("dashboard:screen_edit", args=[other_screen.pk]))

        self.assertEqual(response.status_code, 404)

    @override_settings(DISPLAY_WS_ENABLED=True)
    def test_manual_screen_commands_are_pushed_to_required_group_immediately(self):
        screen = DisplayScreen.objects.create(
            name="الشاشة الرئيسية",
            school=self.school,
            is_active=True,
        )

        for url_name, event_type in (
            ("dashboard:screen_refresh_now", "broadcast_invalidate"),
            ("dashboard:screen_reload_now", "broadcast_reload"),
        ):
            channel_layer = SimpleNamespace(group_send=AsyncMock())
            with patch("channels.layers.get_channel_layer", return_value=channel_layer):
                with self.captureOnCommitCallbacks(execute=True):
                    response = self.client.post(reverse(url_name, args=[screen.pk]))

            self.assertRedirects(
                response,
                reverse("dashboard:screen_list"),
                fetch_redirect_response=False,
            )
            channel_layer.group_send.assert_awaited_once()
            group, event = channel_layer.group_send.await_args.args
            self.assertEqual(group, school_group_name(self.school.pk))
            self.assertEqual(event["type"], event_type)
            self.assertEqual(event["school_id"], self.school.pk)
            self.assertEqual(event["target_screen_id"], screen.pk)

    def test_bound_screen_without_legacy_presence_is_not_called_offline(self):
        DisplayScreen.objects.create(
            name="الشاشة الرئيسية",
            school=self.school,
            is_active=True,
            bound_device_id="tv-device-1",
            bound_at=timezone.now(),
        )

        response = self.client.get(reverse("dashboard:screen_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مرتبطة وجاهزة")
        self.assertContains(response, "قبل أقل من دقيقة")
        self.assertNotContains(response, "مرتبطة بلا اتصال")
        self.assertNotContains(response, "لم تتصل بعد")

    def test_successful_display_binding_lookup_records_presence(self):
        screen = DisplayScreen.objects.create(
            name="الشاشة الرئيسية",
            school=self.school,
            is_active=True,
            bound_device_id="tv-device-1",
        )

        bound_screen = bind_device_atomic(token=screen.token, device_id="tv-device-1")

        self.assertEqual(bound_screen.pk, screen.pk)
        self.assertIsNotNone(latest_display_presence(screen))

    def test_completed_guide_opens_the_school_display(self):
        screen = DisplayScreen.objects.create(name="الشاشة الرئيسية", school=self.school, is_active=True)
        school_class = SchoolClass.objects.create(settings=self.settings, name="1/أ")
        subject = Subject.objects.create(school=self.school, name="الرياضيات")
        teacher = Teacher.objects.create(school=self.school, name="أحمد")
        day = DaySchedule.objects.create(settings=self.settings, weekday=1, periods_count=1)
        Period.objects.create(
            day=day,
            school_class=school_class,
            subject=subject,
            teacher=teacher,
            index=1,
            starts_at=time(8, 0),
            ends_at=time(8, 45),
        )

        response = self.client.get(reverse("dashboard:help_getting_started"))

        self.assertContains(response, "100%")
        self.assertContains(response, "افتح شاشة مدرستك الآن")
        self.assertContains(response, reverse("website:short_display", args=[screen.short_code]))

    def test_dashboard_prioritizes_screen_operation_and_first_launch_state(self):
        screen = DisplayScreen.objects.create(name="شاشة المدخل", school=self.school, is_active=True)

        response = self.client.get(reverse("dashboard:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مركز تشغيل المدرسة")
        self.assertContains(response, "بانتظار أول تشغيل")
        self.assertContains(response, "فتح شاشة المدرسة")
        self.assertContains(response, "إدارة الشاشات")
        self.assertContains(response, reverse("website:short_display", args=[screen.short_code]))
        self.assertContains(response, 'aria-label="تقدم إعداد المنصة"')

    def test_dashboard_reports_a_connected_primary_screen(self):
        screen = DisplayScreen.objects.create(
            name="شاشة المدخل",
            school=self.school,
            is_active=True,
            bound_device_id="customer-tv-1",
        )
        touch_display_presence(screen.pk, token=screen.token)

        response = self.client.get(reverse("dashboard:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "متصلة الآن")
        self.assertContains(response, "الشاشة تعمل وتستقبل تحديثات الجدول والتنبيهات تلقائيًا")

    def test_school_manager_is_returned_from_internal_admin_panel(self):
        response = self.client.get(reverse("dashboard:system_admin_dashboard"), follow=True)

        self.assertRedirects(response, reverse("dashboard:index"))
        self.assertContains(response, "هذه الصفحة مخصصة لفريق إدارة النظام")
        self.assertNotContains(response, "403 Forbidden")

    def test_branded_permission_page_replaces_django_default(self):
        request = RequestFactory().get("/protected/")
        request.user = self.user

        response = permission_denied(request, PermissionDenied("blocked"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "هذه الصفحة ليست ضمن صلاحيات حسابك", status_code=403)
        self.assertContains(response, "العودة إلى لوحة المدرسة", status_code=403)
        self.assertNotContains(response, "403 Forbidden", status_code=403)


class RefactoredRoutingAndPermissionTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.school_a = School.objects.create(name="مدرسة أ", slug="refactor-school-a")
        self.school_b = School.objects.create(name="مدرسة ب", slug="refactor-school-b")
        self.manager = get_user_model().objects.create_user(
            username="refactor_manager",
            password="StrongPass123!",
        )
        self.profile = UserProfile.objects.create(user=self.manager, active_school=self.school_a)
        self.profile.schools.add(self.school_a)
        plan = SubscriptionPlan.objects.create(
            code="refactor-active-plan",
            name="خطة الاختبار",
            price=100,
            duration_days=30,
            max_screens=3,
        )
        SchoolSubscription.objects.create(
            school=self.school_a,
            plan=plan,
            starts_at=timezone.localdate(),
            ends_at=timezone.localdate(),
            status="active",
        )

    def test_auth_and_screen_urls_resolve_to_extracted_modules(self):
        self.assertEqual(resolve(reverse("dashboard:login")).func.__module__, "dashboard.auth_views")
        self.assertEqual(resolve(reverse("dashboard:screen_list")).func.__module__, "dashboard.views_screens")
        self.assertEqual(
            resolve(reverse("dashboard:customer_support_tickets")).func.__module__,
            "dashboard.support_views",
        )

        from dashboard import auth_views, support_views, views, views_screens

        self.assertIs(views.login_view, auth_views.login_view)
        self.assertIs(views.screen_list, views_screens.screen_list)
        self.assertIs(views.customer_support_tickets, support_views.customer_support_tickets)

    def test_active_school_service_sets_request_tenant(self):
        request = self.factory.get("/dashboard/")
        request.user = self.manager
        request.school = None

        school, response = get_active_school_or_redirect(request)

        self.assertIsNone(response)
        self.assertEqual(school, self.school_a)
        self.assertEqual(request.school, self.school_a)

    def test_support_identity_cannot_use_school_manager_decorator(self):
        support = get_user_model().objects.create_user(username="support_refactor", password="StrongPass123!")
        support.groups.add(Group.objects.create(name="Support"))
        support_profile = UserProfile.objects.create(user=support, active_school=self.school_a)
        support_profile.schools.add(self.school_a)
        request = self.factory.get("/dashboard/school-data/")
        request.user = support
        protected_view = manager_required(lambda _request: HttpResponse("ok"))

        with self.assertRaises(PermissionDenied):
            protected_view(request)

    def test_regular_manager_cannot_use_system_staff_or_superuser_decorators(self):
        request = self.factory.get("/dashboard/admin-panel/")
        request.user = self.manager

        with self.assertRaises(PermissionDenied):
            system_staff_required(lambda _request: HttpResponse("ok"))(request)
        with self.assertRaises(PermissionDenied):
            superuser_required(lambda _request: HttpResponse("ok"))(request)

    def test_manager_cannot_delete_another_schools_screen(self):
        other_screen = DisplayScreen.objects.create(school=self.school_b, name="شاشة المدرسة ب")
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("dashboard:screen_delete", kwargs={"pk": other_screen.pk})
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(DisplayScreen.objects.filter(pk=other_screen.pk).exists())

    def test_customer_cannot_open_another_users_support_ticket(self):
        other_user = get_user_model().objects.create_user(username="other_ticket_owner", password="StrongPass123!")
        ticket = SupportTicket.objects.create(
            user=other_user,
            school=self.school_b,
            subject="تذكرة خاصة",
            message="لا يجب أن تظهر لمستخدم آخر",
        )
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse("dashboard:customer_support_ticket_detail", kwargs={"pk": ticket.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_ticket_creation_never_uses_an_unauthorized_active_school(self):
        self.profile.active_school = self.school_b
        self.profile.save(update_fields=["active_school"])
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("dashboard:customer_support_ticket_create"),
            {"subject": "طلب مساعدة", "message": "تفاصيل الطلب"},
        )

        self.assertRedirects(
            response,
            reverse("dashboard:customer_support_tickets"),
            fetch_redirect_response=False,
        )
        ticket = SupportTicket.objects.get(user=self.manager, subject="طلب مساعدة")
        self.assertEqual(ticket.school, self.school_a)


@override_settings(
    CACHES=TEST_CACHES,
    TWO_FACTOR_REQUIRED_FOR_PRIVILEGED=False,
)
class SystemAdminExperienceTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username="admin_experience",
            email="admin-experience@example.com",
            password="StrongPass123!",
        )
        self.school = School.objects.create(name="مدرسة الإدارة", slug="admin-school")
        self.manager = get_user_model().objects.create_user(
            username="school_director",
            email="director@example.com",
            password="StrongPass123!",
        )
        self.profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        self.profile.schools.add(self.school)
        self.plan = SubscriptionPlan.objects.create(
            code="admin-pro",
            name="الباقة الاحترافية",
            price=990,
            duration_days=365,
            max_users=7,
            max_screens=2,
            max_schools=1,
        )
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            ends_at=timezone.localdate() + timedelta(days=12),
            status="active",
        )
        self.client.force_login(self.admin)

    def test_admin_dashboard_prioritizes_daily_operations(self):
        response = self.client.get(reverse("dashboard:system_admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مركز إدارة المنصة")
        self.assertContains(response, "تحتاج إلى انتباهك")
        self.assertContains(response, "اشتراكات تنتهي قريباً")
        self.assertContains(response, "الباقات والأسعار")
        self.assertContains(response, "إحصائيات مسار البيع")
        self.assertContains(response, "متوسط الشاشات/مدرسة")

    def test_admin_lists_expose_school_manager_and_subscription_filters(self):
        admin_profile = UserProfile.objects.create(user=self.admin, active_school=self.school)
        admin_profile.schools.add(self.school)

        schools_response = self.client.get(
            reverse("dashboard:system_schools_list"),
            {"plan": self.plan.pk},
        )
        managers_response = self.client.get(
            reverse("dashboard:system_users_list"),
            {"school": self.school.pk, "role": "manager"},
        )
        subscriptions_response = self.client.get(
            reverse("dashboard:system_subscriptions_list"),
            {"status": "expiring"},
        )

        self.assertContains(schools_response, self.school.name)
        self.assertContains(schools_response, self.plan.name)
        school_row = next(
            school for school in schools_response.context["schools"] if school.pk == self.school.pk
        )
        self.assertEqual(school_row.managers_count, 1)
        self.assertContains(managers_response, self.manager.username)
        self.assertContains(subscriptions_response, "ينتهي قريباً")
        self.assertContains(subscriptions_response, self.school.name)

        dashboard_response = self.client.get(reverse("dashboard:system_admin_dashboard"))
        recent_school = next(
            school for school in dashboard_response.context["recent_schools"] if school.pk == self.school.pk
        )
        self.assertEqual(recent_school.managers_total, 1)

    def test_plan_cards_show_limits_and_usage(self):
        response = self.client.get(reverse("dashboard:system_plans_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.plan.name)
        self.assertContains(response, "اشتراكات سارية")
        self.assertContains(response, "إضافة باقة جديدة")
        self.assertContains(response, "تعديل")
        self.assertContains(response, "حذف")
        self.assertContains(response, "2")
        self.assertContains(response, "7")


    def test_superuser_in_support_group_keeps_plan_management_navigation(self):
        self.admin.groups.add(Group.objects.get_or_create(name="Support")[0])

        response = self.client.get(reverse("dashboard:system_plans_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إدارة الباقات والأسعار")
        self.assertContains(response, reverse("dashboard:system_plan_create"))

    def test_plan_crud_and_activation_controls(self):
        form_response = self.client.get(reverse("dashboard:system_plan_create"))
        self.assertContains(form_response, "وصف مختصر")
        self.assertContains(form_response, "الباقة الموصى بها")
        self.assertContains(form_response, "data-duration-days=\"365\"")
        self.assertContains(form_response, "planPreviewName")
        self.assertContains(form_response, "محتوى بطاقة الباقة")
        self.assertContains(form_response, "مزايا الباقة")
        self.assertNotContains(form_response, "الحد الأقصى للمستخدمين")
        self.assertNotContains(form_response, "max_schools")

        create_response = self.client.post(
            reverse("dashboard:system_plan_create"),
            {
                "name": "الباقة الجديدة",
                "description": "وصف الباقة الجديدة",
                "code": "NEW-PLAN",
                "price": "1499.00",
                "duration_days": "365",
                "max_screens": "3",
                "card_badge_text": "باقة المدارس النشطة",
                "card_duration_text": "مدة خاصة للعميل",
                "card_price_caption": "ريال سعودي / عرض سنوي",
                "card_monthly_text": "قيمة شهرية مخصصة",
                "card_features": "الميزة الأولى\nالميزة الثانية",
                "card_screen_text": "3 شاشات متزامنة",
                "card_cta_text": "اطلب العرض الآن",
                "show_card_badge": "on",
                "show_card_duration": "on",
                "show_monthly_equivalent": "on",
                "show_screen_limit": "on",
                "sort_order": "2",
                "is_featured": "on",
                "is_active": "on",
            },
        )
        self.assertRedirects(create_response, reverse("dashboard:system_plans_list"))
        created_plan = SubscriptionPlan.objects.get(code="new-plan")
        self.assertEqual(created_plan.max_schools, 1)
        self.assertTrue(created_plan.is_featured)
        self.assertEqual(created_plan.description, "وصف الباقة الجديدة")
        self.assertEqual(created_plan.card_badge_text, "باقة المدارس النشطة")
        self.assertEqual(created_plan.card_features, "الميزة الأولى\nالميزة الثانية")
        self.assertEqual(created_plan.card_cta_text, "اطلب العرض الآن")

        edit_response = self.client.post(
            reverse("dashboard:system_plan_edit", args=[created_plan.pk]),
            {
                "name": "الباقة الجديدة المطورة",
                "description": "وصف محدث",
                "code": "new-plan",
                "price": "1799.00",
                "duration_days": "365",
                "max_screens": "4",
                "card_badge_text": "باقة مطورة",
                "card_duration_text": "مدة محدثة",
                "card_price_caption": "سعر محدث",
                "card_monthly_text": "معادل محدث",
                "card_features": "ميزة محدثة",
                "card_screen_text": "4 شاشات متزامنة",
                "card_cta_text": "اطلب النسخة المطورة",
                "show_card_badge": "on",
                "show_card_duration": "on",
                "show_monthly_equivalent": "on",
                "show_screen_limit": "on",
                "sort_order": "2",
                "is_active": "on",
            },
        )
        self.assertRedirects(edit_response, reverse("dashboard:system_plans_list"))
        created_plan.refresh_from_db()
        self.assertEqual(created_plan.name, "الباقة الجديدة المطورة")
        self.assertEqual(created_plan.max_screens, 4)

        toggle_response = self.client.post(
            reverse("dashboard:system_plan_toggle", args=[created_plan.pk])
        )
        self.assertRedirects(toggle_response, reverse("dashboard:system_plans_list"))
        created_plan.refresh_from_db()
        self.assertFalse(created_plan.is_active)

        delete_response = self.client.post(
            reverse("dashboard:system_plan_delete", args=[created_plan.pk])
        )
        self.assertRedirects(delete_response, reverse("dashboard:system_plans_list"))
        self.assertFalse(SubscriptionPlan.objects.filter(pk=created_plan.pk).exists())

    def test_used_plan_is_archived_instead_of_breaking_subscription_history(self):
        response = self.client.get(
            reverse("dashboard:system_plan_delete", args=[self.plan.pk])
        )
        self.assertContains(response, "هذه الباقة مستخدمة")
        self.assertContains(response, "إيقاف الباقة بأمان")

        response = self.client.post(
            reverse("dashboard:system_plan_delete", args=[self.plan.pk])
        )

        self.assertRedirects(response, reverse("dashboard:system_plans_list"))
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.is_active)
        self.assertTrue(SchoolSubscription.objects.filter(plan=self.plan).exists())


@override_settings(
    CACHES=TEST_CACHES,
    TWO_FACTOR_REQUIRED_FOR_PRIVILEGED=False,
)
class SystemEmployeePermissionTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            username="platform_owner",
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.plan = SubscriptionPlan.objects.create(
            code="employee-access-plan",
            name="باقة اختبار الصلاحيات",
            price=500,
            duration_days=365,
            max_screens=2,
        )
        self.school = School.objects.create(
            name="مدرسة صلاحيات الموظفين",
            slug="employee-permissions-school",
        )

    def _create_employee(self, *, username="delegated_employee", permissions=None, role="custom"):
        employee = get_user_model().objects.create_user(
            username=username,
            password="StrongPass123!",
            is_staff=True,
        )
        SystemEmployeeProfile.objects.create(
            user=employee,
            role=role,
            permission_keys=permissions or ["dashboard.view"],
            created_by=self.owner,
        )
        return employee

    def test_owner_can_create_finance_employee_with_granular_permissions(self):
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("dashboard:system_employee_create"),
            {
                "username": "finance_agent",
                "email": "finance@example.com",
                "first_name": "موظف",
                "last_name": "المالية",
                "mobile": "0500000000",
                "is_active": "on",
                "role": ROLE_FINANCE,
                "permissions": [
                    "subscriptions.manage",
                    "subscription_requests.manage",
                    "reports.view",
                ],
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertRedirects(response, reverse("dashboard:system_employees_list"))
        employee = get_user_model().objects.get(username="finance_agent")
        profile = employee.system_employee_profile
        self.assertTrue(employee.is_staff)
        self.assertFalse(employee.is_superuser)
        self.assertEqual(profile.role, ROLE_FINANCE)
        self.assertEqual(
            set(profile.permission_keys),
            {
                "dashboard.view",
                "subscriptions.view",
                "subscriptions.manage",
                "subscription_requests.view",
                "subscription_requests.manage",
                "reports.view",
            },
        )
        self.assertFalse(employee.profile.schools.exists())

    def test_view_only_permission_hides_actions_and_blocks_direct_mutation(self):
        employee = self._create_employee(permissions=["dashboard.view", "plans.view"])
        self.client.force_login(employee)

        list_response = self.client.get(reverse("dashboard:system_plans_list"))
        create_response = self.client.get(reverse("dashboard:system_plan_create"))
        toggle_response = self.client.post(
            reverse("dashboard:system_plan_toggle", args=[self.plan.pk])
        )
        schools_response = self.client.get(reverse("dashboard:system_schools_list"))
        employees_response = self.client.get(reverse("dashboard:system_employees_list"))

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.plan.name)
        self.assertNotContains(list_response, reverse("dashboard:system_plan_create"))
        self.assertEqual(create_response.status_code, 403)
        self.assertEqual(toggle_response.status_code, 403)
        self.assertEqual(schools_response.status_code, 403)
        self.assertEqual(employees_response.status_code, 403)
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.is_active)

    def test_navigation_contains_only_authorized_platform_sections(self):
        employee = self._create_employee(
            permissions=["dashboard.view", "subscriptions.view", "support.view"]
        )
        self.client.force_login(employee)

        response = self.client.get(reverse("dashboard:system_admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        nav_keys = {item["key"] for item in response.context["admin_nav_links"]}
        self.assertEqual(nav_keys, {"home", "subscriptions", "support"})
        self.assertNotContains(response, reverse("dashboard:system_employees_list"))
        self.assertNotContains(response, reverse("dashboard:system_schools_list"))

    def test_owner_edit_applies_permissions_on_the_next_request(self):
        employee = self._create_employee(
            username="support_agent",
            permissions=role_permissions(ROLE_SUPPORT),
            role=ROLE_SUPPORT,
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("dashboard:system_employee_edit", args=[employee.pk]),
            {
                "username": employee.username,
                "email": "support@example.com",
                "first_name": "الدعم",
                "last_name": "الفني",
                "mobile": "",
                "is_active": "on",
                "role": "custom",
                "permissions": ["dashboard.view", "plans.view"],
                "new_password1": "",
                "new_password2": "",
            },
        )
        self.assertRedirects(response, reverse("dashboard:system_employees_list"))

        self.client.force_login(employee)
        self.assertEqual(
            self.client.get(reverse("dashboard:system_plans_list")).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse("dashboard:system_support_tickets")).status_code,
            403,
        )

    def test_delegated_user_manager_cannot_promote_customer_or_list_employees(self):
        employee = self._create_employee(
            permissions=["dashboard.view", "users.view", "users.manage"]
        )
        self.client.force_login(employee)

        create_response = self.client.post(
            reverse("dashboard:system_user_create"),
            {
                "username": "new_school_manager",
                "email": "manager@example.com",
                "first_name": "مدير",
                "last_name": "مدرسة",
                "mobile": "",
                "is_active": "on",
                "is_staff": "on",
                "is_superuser": "on",
                "schools": [self.school.pk],
                "active_school": self.school.pk,
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertRedirects(create_response, reverse("dashboard:system_users_list"))
        customer = get_user_model().objects.get(username="new_school_manager")
        self.assertFalse(customer.is_staff)
        self.assertFalse(customer.is_superuser)

        list_response = self.client.get(
            reverse("dashboard:system_users_list"),
            {"role": "system"},
        )
        self.assertEqual(list_response.status_code, 200)
        listed_usernames = {
            user.username for user in list_response.context["page_obj"].object_list
        }
        self.assertNotIn(self.owner.username, listed_usernames)
        self.assertNotIn(employee.username, listed_usernames)
        self.assertIn(customer.username, listed_usernames)
        self.assertNotContains(list_response, "حسابات النظام")
        self.assertContains(list_response, customer.username)

    def test_view_only_support_and_emergency_access_hides_mutating_controls(self):
        employee = self._create_employee(
            permissions=[
                "dashboard.view",
                "support.view",
                "emergency_alerts.view",
            ]
        )
        ticket = SupportTicket.objects.create(
            user=self.owner,
            school=self.school,
            subject="تذكرة للقراءة فقط",
            message="لا ينبغي أن تظهر أدوات المعالجة.",
        )
        old_alert = EmergencyAlert.objects.create(
            kind=EmergencyAlert.KIND_URGENT,
            title="تنبيه قديم للقراءة فقط",
            message="لا ينبغي أن تظهر أداة حذفه.",
            created_by=self.owner,
            is_active=False,
        )
        old_alert.schools.add(self.school)
        self.client.force_login(employee)

        ticket_response = self.client.get(
            reverse("dashboard:system_support_ticket_detail", args=[ticket.pk])
        )
        emergency_response = self.client.get(reverse("dashboard:emergency_alert_list"))

        self.assertEqual(ticket_response.status_code, 200)
        self.assertContains(ticket_response, ticket.subject)
        self.assertNotContains(ticket_response, "إضافة رد")
        self.assertNotContains(ticket_response, "إرسال الرد")
        self.assertEqual(
            self.client.post(
                reverse("dashboard:system_support_ticket_detail", args=[ticket.pk]),
                {"status": "closed"},
            ).status_code,
            403,
        )
        self.assertEqual(emergency_response.status_code, 200)
        self.assertNotContains(emergency_response, "تنبيه طارئ جديد")
        self.assertNotContains(
            emergency_response,
            reverse("dashboard:emergency_alert_delete", kwargs={"pk": old_alert.pk}),
        )
        self.assertEqual(
            self.client.get(reverse("dashboard:emergency_alert_create")).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse("dashboard:emergency_alert_delete", kwargs={"pk": old_alert.pk})
            ).status_code,
            403,
        )
        self.assertTrue(EmergencyAlert.objects.filter(pk=old_alert.pk).exists())


@override_settings(CACHES=TEST_CACHES)
class EmergencyAlertsAndExcelImportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة الطوارئ", slug="emergency-school")
        self.other_school = School.objects.create(name="مدرسة أخرى للطوارئ", slug="other-emergency-school")
        self.settings = SchoolSettings.objects.create(name=self.school.name, school=self.school)
        self.manager = get_user_model().objects.create_user(
            username="emergency_manager",
            password="StrongPass123!",
        )
        self.profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        self.profile.schools.add(self.school)
        plan = SubscriptionPlan.objects.create(
            code="emergency-test-plan",
            name="باقة اختبار الطوارئ",
            price=100,
            duration_days=30,
            max_screens=5,
        )
        SchoolSubscription.objects.create(
            school=self.school,
            plan=plan,
            starts_at=timezone.localdate(),
            ends_at=timezone.localdate() + timedelta(days=30),
            status="active",
        )
        self.screen = DisplayScreen.objects.create(name="الشاشة الرئيسية", school=self.school)
        self.other_screen = DisplayScreen.objects.create(name="شاشة أخرى", school=self.school)
        self.client.force_login(self.manager)

    def test_manager_can_target_one_screen_and_cancel_with_audit_log(self):
        response = self.client.post(
            reverse("dashboard:emergency_alert_create"),
            {
                "kind": "fire",
                "title": "تنبيه حريق",
                "message": "تنفيذ الإخلاء فورًا",
                "schools": [self.school.pk],
                "scope": "screens",
                "screens": [self.screen.pk],
            },
        )
        self.assertRedirects(
            response,
            reverse("dashboard:emergency_alert_list"),
            fetch_redirect_response=False,
        )
        alert = EmergencyAlert.objects.get()
        self.assertEqual(alert.created_by, self.manager)
        self.assertEqual(list(alert.screens.all()), [self.screen])

        snap = {}
        _merge_real_data_into_snapshot(RequestFactory().get("/api/display/snapshot/"), snap, self.settings)
        self.assertEqual(snap["emergency_alerts"][0]["screen_ids"], [self.screen.pk])

        cancel = self.client.post(
            reverse("dashboard:emergency_alert_cancel", kwargs={"pk": alert.pk})
        )
        self.assertRedirects(
            cancel,
            reverse("dashboard:emergency_alert_list"),
            fetch_redirect_response=False,
        )
        alert.refresh_from_db()
        self.assertFalse(alert.is_active)
        self.assertEqual(alert.cancelled_by, self.manager)
        self.assertIsNotNone(alert.cancelled_at)

    def test_manager_can_delete_an_expired_emergency_alert(self):
        alert = EmergencyAlert.objects.create(
            kind=EmergencyAlert.KIND_URGENT,
            title="تنبيه قديم",
            message="انتهى هذا التنبيه",
            created_by=self.manager,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        alert.schools.add(self.school)

        list_response = self.client.get(reverse("dashboard:emergency_alert_list"))
        self.assertContains(
            list_response,
            reverse("dashboard:emergency_alert_delete", kwargs={"pk": alert.pk}),
        )

        response = self.client.post(
            reverse("dashboard:emergency_alert_delete", kwargs={"pk": alert.pk})
        )

        self.assertRedirects(
            response,
            reverse("dashboard:emergency_alert_list"),
            fetch_redirect_response=False,
        )
        self.assertFalse(EmergencyAlert.objects.filter(pk=alert.pk).exists())

    def test_manager_must_cancel_an_active_emergency_alert_before_deleting_it(self):
        alert = EmergencyAlert.objects.create(
            kind=EmergencyAlert.KIND_FIRE,
            title="تنبيه نشط",
            message="لا يمكن حذفه مباشرة",
            created_by=self.manager,
        )
        alert.schools.add(self.school)

        response = self.client.post(
            reverse("dashboard:emergency_alert_delete", kwargs={"pk": alert.pk})
        )

        self.assertRedirects(
            response,
            reverse("dashboard:emergency_alert_list"),
            fetch_redirect_response=False,
        )
        self.assertTrue(EmergencyAlert.objects.filter(pk=alert.pk).exists())

    def test_manager_cannot_delete_another_schools_emergency_alert(self):
        alert = EmergencyAlert.objects.create(
            kind=EmergencyAlert.KIND_WEATHER,
            title="تنبيه مدرسة أخرى",
            message="خارج نطاق المدير",
            created_by=self.manager,
            is_active=False,
        )
        alert.schools.add(self.other_school)

        response = self.client.post(
            reverse("dashboard:emergency_alert_delete", kwargs={"pk": alert.pk})
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(EmergencyAlert.objects.filter(pk=alert.pk).exists())

    def test_emergency_screen_selection_wins_over_stale_all_scope(self):
        response = self.client.post(
            reverse("dashboard:emergency_alert_create"),
            {
                "kind": "urgent",
                "title": "رسالة لشاشة واحدة",
                "message": "لا تعمم هذه الرسالة",
                "schools": [self.school.pk],
                "scope": "all",
                "screens": [self.screen.pk],
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard:emergency_alert_list"),
            fetch_redirect_response=False,
        )
        self.assertEqual(list(EmergencyAlert.objects.get().screens.all()), [self.screen])

    def test_manager_cannot_send_emergency_to_another_school(self):
        response = self.client.post(
            reverse("dashboard:emergency_alert_create"),
            {
                "kind": "urgent",
                "title": "رسالة",
                "message": "لا يجب إرسالها",
                "schools": [self.other_school.pk],
                "scope": "all",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EmergencyAlert.objects.exists())
        self.assertTrue(response.context["form"].errors["schools"])

    def test_occasion_template_prefills_theme_and_bounded_display_window(self):
        response = self.client.get(
            reverse("dashboard:ann_create"),
            {"template": "national_day"},
        )

        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertEqual(form.initial["occasion_theme"], "national_day")
        self.assertEqual(
            form.initial["expires_at"] - form.initial["starts_at"],
            timedelta(hours=24),
        )
        self.assertContains(response, "الثيم يغيّر ألوان وخلفية وزخارف شاشة العرض")

    def test_occasion_theme_is_saved_and_exposed_to_display_snapshot(self):
        starts_at = timezone.now().replace(second=0, microsecond=0)
        response = self.client.post(
            reverse("dashboard:ann_create"),
            {
                "title": "دام عزك يا وطن",
                "body": "احتفاء باليوم الوطني",
                "level": "success",
                "occasion_theme": "national_day",
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "expires_at": (starts_at + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M"),
                "is_active": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard:ann_list"),
            fetch_redirect_response=False,
        )
        announcement = Announcement.objects.get()
        self.assertEqual(announcement.occasion_theme, "national_day")
        snap = {}
        _merge_real_data_into_snapshot(
            RequestFactory().get("/api/display/snapshot/"),
            snap,
            self.settings,
        )
        self.assertEqual(snap["announcements"][0]["occasion_theme"], "national_day")
        self.assertEqual(
            snap["announcements"][0]["occasion_theme_label"],
            "اليوم الوطني السعودي",
        )

    def test_occasion_and_announcement_can_target_one_screen(self):
        starts_at = timezone.now().replace(second=0, microsecond=0)
        response = self.client.post(
            reverse("dashboard:ann_create"),
            {
                "title": "مناسبة في شاشة المدخل",
                "body": "هذه المناسبة ليست لجميع الشاشات",
                "level": "success",
                "occasion_theme": "national_day",
                "scope": "screens",
                "screens": [self.screen.pk],
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "expires_at": (starts_at + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M"),
                "is_active": "on",
            },
        )

        self.assertRedirects(
            response,
            reverse("dashboard:ann_list"),
            fetch_redirect_response=False,
        )
        announcement = Announcement.objects.get()
        self.assertEqual(list(announcement.screens.all()), [self.screen])
        snap = {}
        _merge_real_data_into_snapshot(
            RequestFactory().get("/api/display/snapshot/"),
            snap,
            self.settings,
        )
        self.assertEqual(snap["announcements"][0]["screen_ids"], [self.screen.pk])

    def test_announcement_target_cannot_use_another_school_screen(self):
        foreign_screen = DisplayScreen.objects.create(
            name="شاشة مدرسة أخرى",
            school=self.other_school,
        )
        starts_at = timezone.now().replace(second=0, microsecond=0)
        response = self.client.post(
            reverse("dashboard:ann_create"),
            {
                "title": "تنبيه غير مسموح",
                "body": "اختبار العزل",
                "level": "info",
                "scope": "screens",
                "screens": [foreign_screen.pk],
                "starts_at": starts_at.strftime("%Y-%m-%dT%H:%M"),
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Announcement.objects.exists())
        self.assertTrue(response.context["form"].errors["screens"])

    def test_excel_template_preview_and_atomic_import_create_full_timetable(self):
        parsed = parse_workbook(BytesIO(build_template_bytes()))
        self.assertEqual(parsed["errors"], [])
        self.assertEqual(len(parsed["rows"]), 1)
        result = apply_import(school=self.school, parsed=parsed)
        self.assertEqual(result["lessons"], 1)
        self.assertTrue(Teacher.objects.filter(school=self.school, name="أحمد محمد").exists())
        self.assertTrue(Subject.objects.filter(school=self.school, name="الرياضيات").exists())
        self.assertTrue(SchoolClass.objects.filter(settings=self.settings, name="الأول أ").exists())
        self.assertTrue(DaySchedule.objects.filter(settings=self.settings, weekday=7).exists())
        self.assertTrue(
            ClassLesson.objects.filter(settings=self.settings, weekday=7, period_index=1).exists()
        )

    def test_excel_import_rejects_invalid_rows_before_writing(self):
        from openpyxl import load_workbook

        wb = load_workbook(BytesIO(build_template_bytes()))
        wb["الجدول"]["A2"] = "يوم غير صحيح"
        broken = BytesIO()
        wb.save(broken)
        broken.seek(0)
        parsed = parse_workbook(broken)
        self.assertTrue(parsed["errors"])
        with self.assertRaises(ValueError):
            apply_import(school=self.school, parsed=parsed)
        self.assertFalse(ClassLesson.objects.filter(settings=self.settings).exists())

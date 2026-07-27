from __future__ import annotations

from datetime import time, timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.test.client import RequestFactory
from django.urls import resolve, reverse
from django.utils import timezone
import pyotp

from core.models import DisplayScreen, School, SubscriptionPlan, SupportTicket, UserProfile, UserTwoFactorAuth
from core.display_presence import latest_display_presence, touch_display_presence
from display.services.device_binding import bind_device_atomic
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
from subscriptions.models import SchoolSubscription
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
            price=100,
            duration_days=365,
            max_screens=3,
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
        create_response = self.client.post(
            reverse("dashboard:system_plan_create"),
            {
                "name": "الباقة الجديدة",
                "code": "NEW-PLAN",
                "price": "1499.00",
                "duration_days": "365",
                "max_schools": "1",
                "max_users": "10",
                "max_screens": "3",
                "sort_order": "2",
                "is_active": "on",
            },
        )
        self.assertRedirects(create_response, reverse("dashboard:system_plans_list"))
        created_plan = SubscriptionPlan.objects.get(code="new-plan")

        edit_response = self.client.post(
            reverse("dashboard:system_plan_edit", args=[created_plan.pk]),
            {
                "name": "الباقة الجديدة المطورة",
                "code": "new-plan",
                "price": "1799.00",
                "duration_days": "365",
                "max_schools": "1",
                "max_users": "15",
                "max_screens": "4",
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

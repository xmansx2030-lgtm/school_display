from __future__ import annotations

from datetime import time

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

from core.models import DisplayScreen, School, SubscriptionPlan, SupportTicket, UserProfile
from core.display_presence import touch_display_presence
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
from schedule.models import DaySchedule, Period, SchoolClass, SchoolSettings, Subject, Teacher


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
        self.assertContains(response, "شوهدت مؤخراً")
        self.assertContains(response, "قبل أقل من دقيقة")
        self.assertNotContains(response, "لم تتصل بعد")

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

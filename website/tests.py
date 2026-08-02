from django.contrib.auth import get_user_model
from django.contrib.auth import get_user
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import DisplayScreen, School, SubscriptionPlan, UserProfile
from schedule.models import SchoolSettings
from subscriptions.models import SchoolSubscription


TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "website-trial-tests",
    }
}


@override_settings(CACHES=TEST_CACHES)
class TrialSignupTests(TestCase):
    def setUp(self):
        cache.clear()

    def _payload(self, **overrides):
        data = {
            "school_name": "مدرسة الاختبار",
            "school_type": "boys",
            "city": "الرياض",
            "contact_name": "أحمد محمد",
            "mobile": "0501234567",
            "email": "manager@example.com",
            "password": "StrongPass123!",
            "password_confirm": "StrongPass123!",
        }
        data.update(overrides)
        return data

    def test_trial_signup_creates_ready_school_account(self):
        response = self.client.post(reverse("website:trial_signup"), data=self._payload())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(School.objects.count(), 1)
        self.assertEqual(UserProfile.objects.count(), 1)
        self.assertEqual(SchoolSubscription.objects.count(), 1)
        self.assertEqual(DisplayScreen.objects.count(), 1)
        self.assertEqual(SchoolSettings.objects.count(), 1)

        school = School.objects.get()
        profile = UserProfile.objects.select_related("active_school").get()
        subscription = SchoolSubscription.objects.select_related("plan", "school").get()

        self.assertEqual(profile.active_school, school)
        self.assertTrue(profile.schools.filter(pk=school.pk).exists())
        self.assertEqual(profile.mobile, "0501234567")
        self.assertEqual(profile.user.email, "manager@example.com")
        self.assertTrue(profile.needs_onboarding)
        self.assertEqual(subscription.school, school)
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.plan.code, "free-trial")
        self.assertEqual(subscription.plan.duration_days, 14)
        self.assertEqual(subscription.plan.max_screens, 1)
        self.assertTrue(get_user_model().objects.get().check_password("StrongPass123!"))
        self.assertEqual(response.json()["username"], get_user_model().objects.get().username)
        self.assertEqual(response.json()["mobile"], "0501234567")
        self.assertIn("login_url", response.json())
        self.assertEqual(response.json()["redirect_url"], reverse("dashboard:help_getting_started"))

    def test_trial_user_can_login_with_mobile_after_auto_signup(self):
        signup = self.client.post(reverse("website:trial_signup"), data=self._payload())
        self.assertEqual(signup.status_code, 200)
        self.client.logout()

        response = self.client.post(
            reverse("dashboard:login"),
            data={"username": "0501234567", "password": "StrongPass123!"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard:help_getting_started"))
        self.assertTrue(get_user(self.client).is_authenticated)

    def test_opening_getting_started_completes_first_login_redirect(self):
        self.client.post(reverse("website:trial_signup"), data=self._payload())
        profile = UserProfile.objects.get()
        self.assertTrue(profile.needs_onboarding)

        response = self.client.get(reverse("dashboard:help_getting_started"))

        self.assertEqual(response.status_code, 200)
        profile.refresh_from_db()
        self.assertFalse(profile.needs_onboarding)

    def test_signup_dialog_exposes_accessible_modal_and_mobile_contract(self):
        response = self.client.get(reverse("website:home"))

        self.assertContains(response, 'role="dialog"')
        self.assertContains(response, 'aria-modal="true"')
        self.assertContains(response, 'class="pricing-sync-note"')
        self.assertContains(response, 'pattern="05[0-9]{8}"')
        self.assertContains(response, 'type="email"')
        self.assertContains(response, "يُستخدم لإرسال الفواتير واستعادة كلمة المرور")

    def test_landing_page_has_conversion_and_performance_contracts(self):
        response = self.client.get(reverse("website:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'rel="canonical" href="https://school-display.com/"')
        self.assertContains(response, 'class="skip-link"')
        self.assertContains(response, 'id="heroTrialCta"')
        self.assertContains(response, 'id="videoLaunch"')
        self.assertContains(response, "بلا بطاقة بنكية")
        self.assertContains(response, "كل المزايا مشمولة")
        self.assertContains(response, "تنبيهات طوارئ تتصدر الشاشة فوراً")
        self.assertContains(response, "استيراد الجدول من Excel")
        self.assertContains(response, "آخر بيانات محفوظة")
        self.assertContains(response, 'data-form-step="1"')
        self.assertContains(response, 'data-form-step="2"')
        self.assertNotContains(response, "<iframe")

    def test_landing_pricing_uses_only_active_dashboard_plans(self):
        active_plan = SubscriptionPlan.objects.create(
            code="landing-annual",
            name="الباقة السنوية المتزامنة",
            description="وصف موحد يظهر في جميع واجهات الباقات",
            price=9876,
            duration_days=365,
            max_schools=2,
            max_users=12,
            max_screens=4,
            sort_order=3,
            is_featured=True,
            is_active=True,
        )
        SubscriptionPlan.objects.create(
            code="landing-hidden",
            name="باقة مخفية من الهبوط",
            price=4321,
            duration_days=180,
            max_schools=1,
            max_users=5,
            max_screens=1,
            is_active=False,
        )

        response = self.client.get(reverse("website:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, active_plan.name)
        self.assertContains(response, active_plan.description)
        self.assertContains(response, "الأكثر طلباً")
        self.assertContains(response, "9876")
        self.assertContains(response, "سنة كاملة")
        self.assertContains(response, "الشاشات:")
        self.assertContains(response, "data-plan-code=\"landing-annual\"")
        self.assertNotContains(response, "المدارس:")
        self.assertNotContains(response, "باقة مخفية من الهبوط")

    def test_landing_pricing_reflects_dashboard_price_update_on_next_request(self):
        plan = SubscriptionPlan.objects.create(
            code="landing-live-price",
            name="باقة السعر اللحظي",
            price=8765,
            duration_days=90,
            max_schools=1,
            max_users=6,
            max_screens=2,
            is_active=True,
        )
        first_response = self.client.get(reverse("website:home"))
        self.assertContains(first_response, "8765")

        plan.price = 7654
        plan.duration_days = 120
        plan.save(update_fields=["price", "duration_days"])

        second_response = self.client.get(reverse("website:home"))
        self.assertContains(second_response, "7654")
        self.assertContains(second_response, "120 يوماً")
        self.assertNotContains(second_response, "8765")
        self.assertIn("no-cache", second_response.headers.get("Cache-Control", ""))

    def test_trial_signup_rejects_duplicate_mobile(self):
        first = self.client.post(reverse("website:trial_signup"), data=self._payload())
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            reverse("website:trial_signup"),
            data=self._payload(school_name="مدرسة أخرى"),
        )

        self.assertEqual(second.status_code, 400)
        self.assertFalse(second.json()["ok"])
        self.assertIn("mobile", second.json()["errors"])
        self.assertEqual(School.objects.count(), 1)
        self.assertEqual(SubscriptionPlan.objects.filter(code="free-trial").count(), 1)

    def test_trial_signup_requires_a_valid_unique_email(self):
        missing = self.client.post(
            reverse("website:trial_signup"),
            data=self._payload(email=""),
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("email", missing.json()["errors"])

        first = self.client.post(reverse("website:trial_signup"), data=self._payload())
        self.assertEqual(first.status_code, 200)

        duplicate = self.client.post(
            reverse("website:trial_signup"),
            data=self._payload(
                school_name="مدرسة بريد أخرى",
                mobile="0512345678",
                email="MANAGER@example.com",
            ),
        )
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("email", duplicate.json()["errors"])


@override_settings(DISPLAY_WS_LIVE_STATUS_CHECK_SEC=60)
class DisplayRuntimeConfigTests(TestCase):
    def test_display_page_receives_the_configured_ws_safety_interval(self):
        school = School.objects.create(name="مدرسة العرض", slug="display-school")
        SchoolSettings.objects.create(name=school.name, school=school)
        screen = DisplayScreen.objects.create(name="الشاشة الرئيسية", school=school)

        response = self.client.get(reverse("website:short_display", args=[screen.short_code]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-ws-live-status-check="60"')

    def test_display_page_exposes_screen_specific_theme_and_visibility(self):
        school = School.objects.create(name="مدرسة التخصيص", slug="custom-display-school")
        SchoolSettings.objects.create(name=school.name, school=school, theme="emerald")
        screen = DisplayScreen.objects.create(
            name="شاشة المدخل",
            school=school,
            theme_override="violet",
            occasion_theme="founding_day",
            featured_panel_override="duty",
            show_announcements=False,
            show_period_classes=True,
            show_standby=False,
            show_duty=True,
            show_excellence=False,
        )

        response = self.client.get(reverse("website:short_display", args=[screen.short_code]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-theme="violet"')
        self.assertContains(response, 'data-screen-theme="violet"')
        self.assertContains(response, 'data-screen-occasion-theme="founding_day"')
        self.assertContains(response, 'data-screen-featured-panel="duty"')
        self.assertContains(response, 'data-screen-show-announcements="0"')
        self.assertContains(response, 'data-screen-show-period-classes="1"')
        self.assertContains(response, 'data-screen-show-standby="0"')
        self.assertContains(response, 'data-screen-show-duty="1"')
        self.assertContains(response, 'data-screen-show-excellence="0"')

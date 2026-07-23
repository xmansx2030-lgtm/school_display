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
        self.assertContains(response, 'aria-label="مدة الاشتراك"')
        self.assertContains(response, 'pattern="05[0-9]{8}"')

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


@override_settings(DISPLAY_WS_LIVE_STATUS_CHECK_SEC=60)
class DisplayRuntimeConfigTests(TestCase):
    def test_display_page_receives_the_configured_ws_safety_interval(self):
        school = School.objects.create(name="مدرسة العرض", slug="display-school")
        SchoolSettings.objects.create(name=school.name, school=school)
        screen = DisplayScreen.objects.create(name="الشاشة الرئيسية", school=school)

        response = self.client.get(reverse("website:short_display", args=[screen.short_code]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-ws-live-status-check="60"')

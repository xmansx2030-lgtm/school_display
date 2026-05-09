from django.contrib.auth import get_user_model
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
        self.assertEqual(subscription.school, school)
        self.assertEqual(subscription.status, "active")
        self.assertEqual(subscription.plan.code, "free-trial")
        self.assertEqual(subscription.plan.duration_days, 14)
        self.assertEqual(subscription.plan.max_screens, 1)
        self.assertTrue(get_user_model().objects.get().check_password("StrongPass123!"))

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

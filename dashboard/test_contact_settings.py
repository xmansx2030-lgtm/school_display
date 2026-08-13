from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from schedule.models import SchoolSettings
from subscriptions.models import SchoolSubscription


class SchoolContactSettingsTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة التواصل", slug="contact-settings-school")
        self.user = get_user_model().objects.create_user(
            username="contact_manager",
            email="old@example.com",
            password="StrongPass123!",
        )
        self.profile = UserProfile.objects.create(
            user=self.user,
            active_school=self.school,
            mobile="0500000000",
        )
        self.profile.schools.add(self.school)
        self.settings = SchoolSettings.objects.create(name=self.school.name, school=self.school)
        plan = SubscriptionPlan.objects.create(
            code="contact-settings-plan",
            name="خطة إعدادات التواصل",
            price=590,
            duration_days=180,
            max_screens=1,
        )
        SchoolSubscription.objects.create(
            school=self.school,
            plan=plan,
            starts_at=timezone.localdate(),
            status="active",
        )
        self.client.force_login(self.user)

    def _settings_payload(self, **overrides):
        payload = {
            "featured_panel": SchoolSettings.FEATURE_PANEL_EXCELLENCE,
            "theme": SchoolSettings.THEME_INDIGO,
            "standby_scroll_speed": "0.8",
            "periods_scroll_speed": "0.5",
            "bell_sound": "bell",
            "screen_offline_threshold_minutes": "10",
            "screen_offline_grace_minutes": "15",
            "screen_offline_cooldown_minutes": "120",
            "screen_offline_max_alerts_per_day": "3",
            "email": "manager@example.com",
            "mobile": "+966 50 222 3333",
        }
        payload.update(overrides)
        return payload

    def test_settings_page_displays_current_email_and_mobile(self):
        response = self.client.get(reverse("dashboard:settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "بيانات التواصل", count=3)
        self.assertNotContains(response, "التواصل والدفع")
        self.assertNotContains(response, "وسائل التواصل والدفع")
        self.assertContains(response, 'name="email"')
        self.assertContains(response, 'value="old@example.com"')
        self.assertContains(response, 'name="mobile"')
        self.assertContains(response, 'value="0500000000"')
        self.assertContains(response, 'name="bell_sound"')
        self.assertNotContains(response, 'name="weekly_uptime_report_enabled"')

    def test_manager_can_update_contact_details(self):
        response = self.client.post(
            reverse("dashboard:settings"),
            self._settings_payload(),
        )

        self.assertRedirects(
            response,
            reverse("dashboard:settings"),
            fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.user.email, "manager@example.com")
        self.assertEqual(self.profile.mobile, "0502223333")

    def test_invalid_mobile_keeps_contact_tab_open_and_shows_error(self):
        response = self.client.post(
            reverse("dashboard:settings"),
            self._settings_payload(mobile="1234"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-initial-settings-tab="contact"')
        self.assertContains(response, "أدخل رقم جوال سعودي صحيحًا")
        self.user.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.user.email, "old@example.com")
        self.assertEqual(self.profile.mobile, "0500000000")

"""Home facts must describe the running school day and subscription."""
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from schedule.models import SchoolSettings
from standby.models import StandbyAssignment
from subscriptions.models import SchoolSubscription


class HomeFactsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="مدرسة بيانات الرئيسية", slug="home-facts")
        cls.user = get_user_model().objects.create_user(username="home-facts-manager", password="Test-only-pass-2026!")
        profile = UserProfile.objects.create(user=cls.user, active_school=cls.school)
        profile.schools.add(cls.school)
        cls.school_settings = SchoolSettings.objects.create(school=cls.school, name=cls.school.name)
        cls.plan = SubscriptionPlan.objects.create(code="home-facts", name="خطة", price=100, duration_days=365)
        cls.subscription = SchoolSubscription.objects.create(
            school=cls.school, plan=cls.plan, starts_at=timezone.localdate()-timedelta(days=30),
            ends_at=timezone.localdate()+timedelta(days=90), status="active",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_future_pending_renewal_does_not_replace_current_subscription(self):
        SchoolSubscription.objects.create(
            school=self.school, plan=self.plan, starts_at=timezone.localdate()+timedelta(days=91), status="pending",
        )
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["subscription"].pk, self.subscription.pk)

    def test_later_cancelled_record_does_not_hide_active_subscription(self):
        SchoolSubscription.objects.create(
            school=self.school, plan=self.plan, starts_at=timezone.localdate()-timedelta(days=2), status="cancelled",
        )
        response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.context["subscription"].pk, self.subscription.pk)

    def test_school_day_and_today_count_follow_school_timezone(self):
        self.school_settings.timezone_name = "Pacific/Auckland"
        self.school_settings.save(update_fields=["timezone_name"])
        app_date = timezone.localdate()
        school_date = app_date + timedelta(days=1)
        StandbyAssignment.objects.create(school=self.school, date=school_date, period_index=1,
                                         teacher_name="معلم اليوم", class_name="أول / أ")
        instant = datetime.combine(app_date, time(12, 30), tzinfo=datetime_timezone.utc)
        with patch("django.utils.timezone.now", return_value=instant):
            self.client.force_login(self.user)
            response = self.client.get(reverse("dashboard:index"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["today"], school_date)
        self.assertEqual(response.context["stats"]["standby_today"], 1)
        self.assertEqual(response.context["today_weekday_label"], {
            1: "الاثنين", 2: "الثلاثاء", 3: "الأربعاء", 4: "الخميس",
            5: "الجمعة", 6: "السبت", 7: "الأحد",
        }[school_date.isoweekday()])


class SubscriptionDateFactsTests(SimpleTestCase):
    def test_future_subscription_is_not_active_before_its_start(self):
        today = timezone.localdate()
        subscription = SchoolSubscription(status="active", starts_at=today+timedelta(days=1), ends_at=today+timedelta(days=30))
        self.assertFalse(subscription.is_active)

    def test_start_and_end_dates_are_inclusive(self):
        today = timezone.localdate()
        self.assertTrue(SchoolSubscription(status="active", starts_at=today, ends_at=today).is_active)

    def test_remaining_days_use_the_application_date(self):
        subscription = SchoolSubscription(starts_at=date(2026, 12, 1), ends_at=date(2027, 1, 11))
        with patch("subscriptions.models.timezone.localdate", return_value=date(2027, 1, 1)):
            self.assertEqual(subscription.days_left, 10)

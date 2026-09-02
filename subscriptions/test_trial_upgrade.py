"""A school that upgrades mid-trial must stop being warned about the trial.

Paying for a plan creates a second ``SchoolSubscription`` row instead of
editing the trial the customer is still inside, so for the rest of the trial
window the school owns two live terms. Every rule that reads one row in
isolation — the expiry reminder, the dashboard banner — used to speak about the
term the customer had already replaced.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from dashboard.context_processors import school_whatsapp_contact
from subscriptions.email_notifications import enqueue_expiry_email_reminders
from subscriptions.models import (
    SchoolSubscription,
    SubscriptionEmailNotification,
    SubscriptionScreenAddon,
)
from subscriptions.trials import close_superseded_trials
from subscriptions.utils import school_has_active_subscription


class TrialUpgradeTestCase(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.school = School.objects.create(name="مدرسة الترقية", slug="upgrade-school")
        self.trial_plan = SubscriptionPlan.objects.create(
            code="free-trial-14",
            name="تجربة مجانية 14 يوم",
            price=Decimal("0.00"),
            duration_days=14,
            max_screens=1,
        )
        self.paid_plan = SubscriptionPlan.objects.create(
            code="paid-annual",
            name="الباقة السنوية",
            price=Decimal("2150.00"),
            duration_days=365,
            max_screens=5,
        )
        self.user = get_user_model().objects.create_user(
            username="upgrade_manager",
            email="upgrade@example.com",
            password="StrongPass123!",
        )
        profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        profile.schools.add(self.school)

    def _trial(self, *, starts_at=None, days=14):
        starts_at = starts_at or self.today
        return SchoolSubscription.objects.create(
            school=self.school,
            plan=self.trial_plan,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=days),
            status="active",
        )

    def _paid(self, *, starts_at=None, days=365):
        starts_at = starts_at or self.today
        return SchoolSubscription.objects.create(
            school=self.school,
            plan=self.paid_plan,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(days=days),
            status="active",
        )


@override_settings(
    TRANSACTIONAL_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
    EMAIL_SUBSCRIPTION_EXPIRY_DAYS=(7, 3, 1, 0),
    SITE_BASE_URL="https://school-display.com",
)
class ExpiryReminderSupersessionTests(TrialUpgradeTestCase):
    def test_no_reminder_for_a_trial_a_paid_plan_took_over(self):
        trial = self._trial(starts_at=self.today - timedelta(days=11))  # ends in 3 days
        self._paid()

        queued = enqueue_expiry_email_reminders(on_date=self.today)

        self.assertEqual(queued, 0)
        self.assertFalse(
            SubscriptionEmailNotification.objects.filter(subscription=trial).exists()
        )

    def test_no_reminder_when_the_paid_plan_starts_the_day_the_trial_ends(self):
        """Cover that resumes the next day leaves no uncovered day to warn about."""
        trial = self._trial(starts_at=self.today - timedelta(days=11))
        self._paid(starts_at=trial.ends_at + timedelta(days=1))

        self.assertEqual(enqueue_expiry_email_reminders(on_date=self.today), 0)

    def test_a_real_gap_still_warns_the_customer(self):
        trial = self._trial(starts_at=self.today - timedelta(days=11))
        self._paid(starts_at=trial.ends_at + timedelta(days=2))

        queued = enqueue_expiry_email_reminders(on_date=self.today)

        self.assertEqual(queued, 1)
        notification = SubscriptionEmailNotification.objects.get(subscription=trial)
        self.assertEqual(notification.reminder_days, 3)

    def test_a_lone_lapsing_subscription_still_warns(self):
        subscription = self._paid(starts_at=self.today - timedelta(days=362))  # ends in 3 days

        queued = enqueue_expiry_email_reminders(on_date=self.today)

        self.assertEqual(queued, 1)
        self.assertTrue(
            SubscriptionEmailNotification.objects.filter(subscription=subscription).exists()
        )

    def test_an_early_renewal_silences_the_reminder_for_the_running_term(self):
        """The same rule covers a paid customer who renewed ahead of time."""
        running = self._paid(starts_at=self.today - timedelta(days=364))  # ends tomorrow
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.paid_plan,
            starts_at=running.ends_at + timedelta(days=1),
            ends_at=running.ends_at + timedelta(days=366),
            status="active",
        )

        self.assertEqual(enqueue_expiry_email_reminders(on_date=self.today), 0)


class CloseSupersededTrialsTests(TrialUpgradeTestCase):
    def test_a_trial_overtaken_today_is_closed_immediately(self):
        trial = self._trial(starts_at=self.today - timedelta(days=3))
        self._paid()

        result = close_superseded_trials(self.school.pk, on_date=self.today)

        trial.refresh_from_db()
        self.assertEqual(result.trimmed, 1)
        self.assertEqual(result.expired, 1)
        self.assertEqual(trial.status, "expired")
        self.assertEqual(trial.ends_at, self.today - timedelta(days=1))
        self.assertEqual(trial.closure_reason, "upgraded")
        self.assertIsNotNone(trial.closed_at)

    def test_the_customer_keeps_every_day_before_the_paid_plan_starts(self):
        trial = self._trial()
        paid_start = self.today + timedelta(days=5)
        self._paid(starts_at=paid_start)

        close_superseded_trials(self.school.pk, on_date=self.today)

        trial.refresh_from_db()
        self.assertEqual(trial.status, "active")
        self.assertEqual(trial.ends_at, paid_start - timedelta(days=1))
        self.assertTrue(school_has_active_subscription(self.school.pk, on_date=self.today))

    def test_a_paid_plan_starting_after_the_trial_ends_changes_nothing(self):
        trial = self._trial()
        self._paid(starts_at=trial.ends_at + timedelta(days=3))
        original_end = trial.ends_at

        result = close_superseded_trials(self.school.pk, on_date=self.today)

        trial.refresh_from_db()
        self.assertEqual(result.trimmed, 0)
        self.assertEqual(trial.ends_at, original_end)
        self.assertEqual(trial.status, "active")

    def test_the_sweep_is_idempotent(self):
        self._trial(starts_at=self.today - timedelta(days=3))
        self._paid()

        close_superseded_trials(self.school.pk, on_date=self.today)
        second = close_superseded_trials(self.school.pk, on_date=self.today)

        self.assertEqual(second.trimmed, 0)

    def test_a_trial_carrying_paid_screen_addons_is_left_alone(self):
        """Trimming it would silently void screens the customer paid for."""
        trial = self._trial()
        SubscriptionScreenAddon.objects.create(
            subscription=trial,
            screens_added=2,
            status="paid",
            starts_at=self.today,
            ends_at=trial.ends_at,
        )
        self._paid()
        original_end = trial.ends_at

        result = close_superseded_trials(self.school.pk, on_date=self.today)

        trial.refresh_from_db()
        self.assertEqual(result.trimmed, 0)
        self.assertEqual(trial.ends_at, original_end)

    def test_the_school_stays_active_after_its_trial_is_retired(self):
        self._trial(starts_at=self.today - timedelta(days=3))
        self._paid()

        close_superseded_trials(self.school.pk, on_date=self.today)

        self.school.refresh_from_db()
        self.assertTrue(self.school.is_active)
        self.assertTrue(school_has_active_subscription(self.school.pk, on_date=self.today))

    def test_the_sweep_covers_every_school_when_no_id_is_given(self):
        self._trial(starts_at=self.today - timedelta(days=3))
        self._paid()

        result = close_superseded_trials(on_date=self.today)

        self.assertEqual(result.trimmed, 1)
        self.assertEqual(result.schools, 1)

    def test_a_paid_subscription_is_never_trimmed(self):
        running = self._paid(starts_at=self.today - timedelta(days=300))
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.paid_plan,
            starts_at=self.today,
            ends_at=self.today + timedelta(days=365),
            status="active",
        )

        close_superseded_trials(self.school.pk, on_date=self.today)

        running.refresh_from_db()
        self.assertEqual(running.status, "active")
        self.assertEqual(running.ends_at, self.today + timedelta(days=65))


class DashboardSubscriptionBannerTests(TrialUpgradeTestCase):
    def _context(self):
        request = RequestFactory().get("/dashboard/")
        request.user = self.user
        request.school = self.school
        context = school_whatsapp_contact(request)
        return request, context

    def test_the_banner_follows_the_furthest_reaching_subscription(self):
        self._trial(starts_at=self.today - timedelta(days=11))  # ends in 3 days
        paid = self._paid()

        request, context = self._context()

        self.assertEqual(request.school_subscription.pk, paid.pk)
        self.assertIn("متبقي 365 يوم", context["school_whatsapp_status"])

    def test_a_school_on_the_trial_alone_still_sees_its_own_countdown(self):
        trial = self._trial(starts_at=self.today - timedelta(days=11))

        request, context = self._context()

        self.assertEqual(request.school_subscription.pk, trial.pk)
        self.assertIn("متبقي 3 يوم", context["school_whatsapp_status"])

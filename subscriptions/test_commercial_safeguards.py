"""Tests for the commercial safeguards added for launch.

Covers the three ways the platform could previously give service away or take
money without delivering it:

1. Screens kept serving content after a subscription lapsed.
2. Subscriptions never transitioned to ``expired`` on their own.
3. A Moyasar payment whose callback never arrived was never reconciled.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import DisplayScreen, School, SubscriptionPlan, UserProfile
from schedule.models import SchoolSettings

from .access import (
    invalidate_school_subscription_cache,
    school_max_screens,
    school_subscription_is_active,
)
from .expiry import expire_due_subscriptions
from .models import MoyasarCheckout, SchoolSubscription, SubscriptionScreenAddon


def _plan(code: str, *, screens: int | None = 2, price: str = "500.00") -> SubscriptionPlan:
    return SubscriptionPlan.objects.create(
        code=code,
        name=f"خطة {code}",
        price=Decimal(price),
        duration_days=365,
        max_screens=screens,
    )


class SubscriptionAccessCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة الكاش", slug="cache-school")
        self.plan = _plan("cache-plan")

    def test_reports_inactive_without_subscription(self):
        self.assertFalse(school_subscription_is_active(self.school.id))
        self.assertEqual(school_max_screens(self.school.id), 0)

    def test_activation_invalidates_cached_answer(self):
        self.assertFalse(school_subscription_is_active(self.school.id))

        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )

        # The post_save signal must have dropped the cached "inactive" answer.
        self.assertTrue(school_subscription_is_active(self.school.id))
        self.assertEqual(school_max_screens(self.school.id), 2)

    def test_paid_addon_raises_effective_screen_limit(self):
        subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )
        self.assertEqual(school_max_screens(self.school.id), 2)

        SubscriptionScreenAddon.objects.create(
            subscription=subscription,
            screens_added=3,
            status="paid",
            starts_at=timezone.localdate(),
        )
        self.assertEqual(school_max_screens(self.school.id), 5)

    def test_missing_school_is_never_active(self):
        self.assertFalse(school_subscription_is_active(None))
        self.assertEqual(school_max_screens(None), 0)

    def test_invalidate_is_safe_for_missing_school(self):
        invalidate_school_subscription_cache(None)


@override_settings(DISPLAY_REQUIRE_ACTIVE_SUBSCRIPTION=True)
class DisplaySubscriptionGateTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة العرض", slug="display-school")
        SchoolSettings.objects.create(school=self.school, name=self.school.name)
        self.plan = _plan("gate-plan")
        self.screen = DisplayScreen.objects.create(
            school=self.school,
            name="شاشة الإدارة",
            is_active=True,
        )

    def _status_url(self) -> str:
        return f"/api/display/status/{self.screen.token}/"

    def test_active_subscription_is_served(self):
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )
        response = self.client.get(self._status_url())
        self.assertNotEqual(response.status_code, 402)

    def test_screen_without_subscription_is_blocked(self):
        response = self.client.get(self._status_url())
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["error"], "subscription_inactive")
        self.assertIn("no-store", response["Cache-Control"])

    def test_lapsed_subscription_is_blocked(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=yesterday - timedelta(days=30),
            ends_at=yesterday,
            status="active",
        )
        response = self.client.get(self._status_url())
        self.assertEqual(response.status_code, 402)

    def test_gate_can_be_disabled_for_diagnostics(self):
        with override_settings(DISPLAY_REQUIRE_ACTIVE_SUBSCRIPTION=False):
            cache.clear()
            response = self.client.get(self._status_url())
        self.assertNotEqual(response.status_code, 402)

    def test_billing_lookup_failure_does_not_black_out_screens(self):
        with patch(
            "subscriptions.access.school_subscription_is_active",
            side_effect=RuntimeError("redis down"),
        ):
            response = self.client.get(self._status_url())
        self.assertNotEqual(response.status_code, 402)

    def test_display_page_shows_renewal_notice(self):
        response = self.client.get(f"/s/{self.screen.short_code}/")

        self.assertEqual(response.status_code, 402)
        self.assertTemplateUsed(response, "website/display_subscription_inactive.html")
        self.assertContains(response, "الاشتراك غير نشط", status_code=402)

    def test_display_page_renders_normally_when_paid(self):
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )
        response = self.client.get(f"/s/{self.screen.short_code}/")

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "website/display.html")


class SubscriptionExpiryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة الانتهاء", slug="expiry-school")
        self.plan = _plan("expiry-plan", screens=2)
        self.yesterday = timezone.localdate() - timedelta(days=1)

    def _lapsed(self) -> SchoolSubscription:
        return SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=self.yesterday - timedelta(days=30),
            ends_at=self.yesterday,
            status="active",
        )

    def test_lapsed_subscription_becomes_expired(self):
        subscription = self._lapsed()

        result = expire_due_subscriptions()

        subscription.refresh_from_db()
        self.assertEqual(subscription.status, "expired")
        self.assertIsNotNone(subscription.closed_at)
        self.assertEqual(result.expired, 1)
        self.assertEqual(result.schools_synced, 1)

    def test_school_is_deactivated_and_screens_switched_off(self):
        self._lapsed()
        screen = DisplayScreen.objects.create(school=self.school, name="شاشة", is_active=True)

        result = expire_due_subscriptions()

        self.school.refresh_from_db()
        screen.refresh_from_db()
        self.assertFalse(self.school.is_active)
        self.assertFalse(screen.is_active)
        self.assertTrue(screen.auto_disabled_by_limit)
        self.assertEqual(result.screens_disabled, 1)

    def test_cached_access_is_invalidated_on_expiry(self):
        """A cached "active" answer must not survive the expiry run."""
        today = timezone.localdate()
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=today - timedelta(days=30),
            ends_at=today,
            status="active",
        )
        # Warm the cache while the subscription is genuinely still active.
        self.assertTrue(school_subscription_is_active(self.school.id))

        # Run the job as it would run tomorrow, when the term has lapsed.
        expire_due_subscriptions(on_date=today + timedelta(days=1))

        self.assertFalse(school_subscription_is_active(self.school.id))

    def test_open_ended_and_future_subscriptions_are_untouched(self):
        open_ended = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=self.yesterday,
            ends_at=None,
            status="active",
        )
        future = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            ends_at=timezone.localdate() + timedelta(days=30),
            status="active",
        )

        self.assertEqual(expire_due_subscriptions().expired, 0)

        open_ended.refresh_from_db()
        future.refresh_from_db()
        self.assertEqual(open_ended.status, "active")
        self.assertEqual(future.status, "active")

    def test_dry_run_changes_nothing(self):
        subscription = self._lapsed()

        result = expire_due_subscriptions(dry_run=True)

        subscription.refresh_from_db()
        self.assertEqual(result.expired, 1)
        self.assertEqual(subscription.status, "active")

    def test_is_idempotent(self):
        self._lapsed()
        expire_due_subscriptions()
        self.assertEqual(expire_due_subscriptions().expired, 0)


@override_settings(
    DEBUG=True,
    MOYASAR_ENABLED=True,
    MOYASAR_LIVE_MODE=False,
    MOYASAR_ACTIVATE_TEST_PAYMENTS=True,
    MOYASAR_API_BASE_URL="https://api.moyasar.com/v1",
    MOYASAR_PUBLISHABLE_KEY="pk_test_publishable",
    MOYASAR_SECRET_KEY="sk_test_secret",
    MOYASAR_RECONCILIATION_LOOKBACK_HOURS=72,
)
class MoyasarReconciliationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة التسوية", slug="reconcile-school")
        self.plan = _plan("reconcile-plan", price="500.00")
        self.user = get_user_model().objects.create_user(
            username="reconcile_manager",
            password="StrongPass123!",
            email="reconcile@example.com",
        )
        profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        profile.schools.add(self.school)

    def _checkout(self, **overrides) -> MoyasarCheckout:
        defaults = dict(
            school=self.school,
            created_by=self.user,
            plan=self.plan,
            request_type="new",
            starts_at=timezone.localdate(),
            amount=Decimal("500.00"),
            currency="SAR",
            live_mode=False,
            status="initiated",
        )
        defaults.update(overrides)
        return MoyasarCheckout.objects.create(**defaults)

    def _payment(self, checkout: MoyasarCheckout, *, payment_id="pay_abc123", status="paid") -> dict:
        return {
            "id": payment_id,
            "status": status,
            "amount": 50000,
            "currency": "SAR",
            "live": False,
            "metadata": {"merchant_reference": checkout.merchant_reference},
        }

    def test_orphan_payment_is_matched_and_activated(self):
        """The customer paid, then closed the browser before the callback."""
        checkout = self._checkout()
        payment = self._payment(checkout)

        with patch("subscriptions.moyasar.MoyasarClient") as client_cls:
            client = client_cls.return_value
            client.list_payments.return_value = ([payment], False)
            from .moyasar_processing import reconcile_pending_checkouts

            result = reconcile_pending_checkouts()

        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "paid")
        self.assertEqual(checkout.payment_id, "pay_abc123")
        self.assertIsNotNone(checkout.payment_operation_id)
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.activated, 1)
        self.assertTrue(
            SchoolSubscription.objects.filter(school=self.school, status="active").exists()
        )

    def test_known_payment_id_is_refetched(self):
        checkout = self._checkout(payment_id="pay_known", status="authorized")
        payment = self._payment(checkout, payment_id="pay_known")

        with patch("subscriptions.moyasar.MoyasarClient") as client_cls:
            client = client_cls.return_value
            client.fetch_payment.return_value = payment
            client.list_payments.return_value = ([], False)
            from .moyasar_processing import reconcile_pending_checkouts

            result = reconcile_pending_checkouts()

        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "paid")
        self.assertEqual(result.checked, 1)
        self.assertEqual(result.activated, 1)

    def test_amount_mismatch_never_activates(self):
        checkout = self._checkout()
        payment = self._payment(checkout)
        payment["amount"] = 100  # 1.00 SAR against a 500.00 SAR order

        with patch("subscriptions.moyasar.MoyasarClient") as client_cls:
            client = client_cls.return_value
            client.list_payments.return_value = ([payment], False)
            from .moyasar_processing import reconcile_pending_checkouts

            result = reconcile_pending_checkouts()

        checkout.refresh_from_db()
        self.assertIsNone(checkout.payment_operation_id)
        self.assertEqual(checkout.last_event, "reconcile_mismatch")
        self.assertEqual(result.failed, 1)
        self.assertFalse(SchoolSubscription.objects.filter(school=self.school).exists())

    def test_unrelated_payments_are_ignored(self):
        checkout = self._checkout()
        stranger = {
            "id": "pay_other",
            "status": "paid",
            "amount": 50000,
            "currency": "SAR",
            "live": False,
            "metadata": {"merchant_reference": "MS-not-ours"},
        }

        with patch("subscriptions.moyasar.MoyasarClient") as client_cls:
            client = client_cls.return_value
            client.list_payments.return_value = ([stranger], False)
            from .moyasar_processing import reconcile_pending_checkouts

            result = reconcile_pending_checkouts()

        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "initiated")
        self.assertEqual(result.matched, 0)

    def test_abandoned_checkout_is_voided_after_lookback(self):
        checkout = self._checkout()
        MoyasarCheckout.objects.filter(pk=checkout.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )

        with patch("subscriptions.moyasar.MoyasarClient") as client_cls:
            client = client_cls.return_value
            client.list_payments.return_value = ([], False)
            from .moyasar_processing import reconcile_pending_checkouts

            result = reconcile_pending_checkouts()

        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "voided")
        self.assertEqual(checkout.last_event, "reconcile_expired")
        self.assertEqual(result.expired, 1)

    def test_disabled_gateway_does_no_work(self):
        self._checkout()
        with override_settings(MOYASAR_ENABLED=False):
            from .moyasar_processing import reconcile_pending_checkouts

            result = reconcile_pending_checkouts()
        self.assertEqual(result.touched, 0)

"""Tests for buying extra screens through the payment gateway.

Before this existed, the pricing page advertised screen-count pricing that
could only be fulfilled by an administrator editing the database by hand.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile

from .access import school_max_screens
from .models import MoyasarCheckout, SchoolSubscription, SubscriptionScreenAddon
from .moyasar_processing import apply_payment_details
from .pricing import (
    MAX_EXTRA_SCREENS,
    checkout_total,
    normalize_extra_screens,
    prorated_screen_addon_price,
    screen_addon_price,
)


@override_settings(
    SCREEN_ADDON_MONTHLY_PRICE=60,
    SCREEN_ADDON_SEMIANNUAL_MULTIPLIER=6,
    SCREEN_ADDON_ANNUAL_MULTIPLIER=10,
)
class PricingTests(TestCase):
    def test_monthly_term_bills_one_month_per_screen(self):
        self.assertEqual(screen_addon_price(2, duration_days=30), Decimal("120.00"))

    def test_semiannual_term_bills_six_months(self):
        self.assertEqual(screen_addon_price(1, duration_days=180), Decimal("360.00"))

    def test_annual_term_bills_ten_months(self):
        """Two months free, matching the discount on the plan itself."""
        self.assertEqual(screen_addon_price(1, duration_days=365), Decimal("600.00"))

    def test_zero_screens_cost_nothing(self):
        self.assertEqual(screen_addon_price(0, duration_days=365), Decimal("0.00"))

    def test_checkout_total_combines_plan_and_screens(self):
        plan = SubscriptionPlan.objects.create(
            code="combo",
            name="خطة",
            price=Decimal("1000.00"),
            duration_days=365,
        )
        self.assertEqual(checkout_total(plan, 2), Decimal("2200.00"))

    def test_untrusted_screen_counts_are_clamped(self):
        self.assertEqual(normalize_extra_screens("3"), 3)
        self.assertEqual(normalize_extra_screens("-5"), 0)
        self.assertEqual(normalize_extra_screens("abc"), 0)
        self.assertEqual(normalize_extra_screens(None), 0)
        self.assertEqual(normalize_extra_screens("99999"), MAX_EXTRA_SCREENS)

    def test_midterm_purchase_is_prorated(self):
        plan = SubscriptionPlan.objects.create(
            code="prorate",
            name="سنوية",
            price=Decimal("1000.00"),
            duration_days=365,
        )
        today = timezone.localdate()

        # Half the annual term left: half of the 600.00 annual screen price.
        half = prorated_screen_addon_price(
            1,
            plan=plan,
            starts_at=today,
            ends_at=today + timedelta(days=181),
        )
        self.assertEqual(half, Decimal("299.18"))

    def test_full_remaining_term_is_not_discounted_twice(self):
        plan = SubscriptionPlan.objects.create(
            code="full-term",
            name="سنوية",
            price=Decimal("1000.00"),
            duration_days=365,
        )
        today = timezone.localdate()
        price = prorated_screen_addon_price(
            1,
            plan=plan,
            starts_at=today,
            ends_at=today + timedelta(days=400),
        )
        self.assertEqual(price, Decimal("600.00"))

    def test_open_ended_subscription_pays_the_full_term_price(self):
        plan = SubscriptionPlan.objects.create(
            code="open",
            name="مفتوحة",
            price=Decimal("1000.00"),
            duration_days=365,
        )
        price = prorated_screen_addon_price(
            1,
            plan=plan,
            starts_at=timezone.localdate(),
            ends_at=None,
        )
        self.assertEqual(price, Decimal("600.00"))


@override_settings(
    DEBUG=True,
    MOYASAR_ENABLED=True,
    MOYASAR_LIVE_MODE=False,
    MOYASAR_ACTIVATE_TEST_PAYMENTS=True,
    MOYASAR_API_BASE_URL="https://api.moyasar.com/v1",
    MOYASAR_PUBLISHABLE_KEY="pk_test_publishable",
    MOYASAR_SECRET_KEY="sk_test_secret",
    MOYASAR_CALLBACK_BASE_URL="https://school-display.com",
    SCREEN_ADDON_MONTHLY_PRICE=60,
    SCREEN_ADDON_ANNUAL_MULTIPLIER=10,
)
class ScreenPurchaseCheckoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة الشاشات", slug="screens-school")
        self.plan = SubscriptionPlan.objects.create(
            code="screens-plan",
            name="الخطة السنوية",
            price=Decimal("1000.00"),
            duration_days=365,
            max_screens=2,
        )
        self.user = get_user_model().objects.create_user(
            username="screens_manager",
            password="StrongPass123!",
            email="screens@example.com",
        )
        profile = UserProfile.objects.create(
            user=self.user,
            active_school=self.school,
            email_verified_at=timezone.now(),
        )
        profile.schools.add(self.school)
        self.client.force_login(self.user)

    def _start(self, **payload):
        return self.client.post(reverse("subscriptions:moyasar_start"), payload)

    def _pay(self, checkout):
        details = {
            "id": f"pay_{checkout.pk}",
            "status": "paid",
            "amount": int(checkout.amount * 100),
            "currency": "SAR",
            "live": False,
            "metadata": {"merchant_reference": checkout.merchant_reference},
        }
        return apply_payment_details(checkout.pk, details, event_type="return")

    def _activate(self, *, ends_at=None):
        return SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            ends_at=ends_at,
            status="active",
        )

    # ---- buying screens alongside a plan ---------------------------------
    def test_new_subscription_can_include_extra_screens(self):
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="3")

        checkout = MoyasarCheckout.objects.get()
        self.assertEqual(checkout.extra_screens, 3)
        # 1000 plan + (3 screens x 60 x 10 months)
        self.assertEqual(checkout.amount, Decimal("2800.00"))

    def test_paying_grants_the_screens(self):
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="3")
        checkout = MoyasarCheckout.objects.get()

        self._pay(checkout)

        addon = SubscriptionScreenAddon.objects.get()
        self.assertEqual(addon.screens_added, 3)
        self.assertEqual(addon.status, "paid")
        self.assertEqual(addon.total_price, Decimal("1800.00"))
        # Plan allows 2, purchase adds 3.
        self.assertEqual(school_max_screens(self.school.id), 5)

    def test_plan_without_extra_screens_creates_no_addon(self):
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="0")
        checkout = MoyasarCheckout.objects.get()

        self._pay(checkout)

        self.assertFalse(SubscriptionScreenAddon.objects.exists())
        self.assertEqual(school_max_screens(self.school.id), 2)

    # ---- buying screens mid-term ------------------------------------------
    def test_midterm_screen_purchase_is_prorated_and_granted(self):
        today = timezone.localdate()
        subscription = self._activate(ends_at=today + timedelta(days=181))

        self._start(request_type="screens", extra_screens="1")
        checkout = MoyasarCheckout.objects.get()
        self.assertEqual(checkout.request_type, "screens")
        self.assertEqual(checkout.amount, Decimal("299.18"))

        self._pay(checkout)

        addon = SubscriptionScreenAddon.objects.get()
        self.assertEqual(addon.subscription, subscription)
        self.assertEqual(addon.screens_added, 1)
        self.assertEqual(addon.starts_at, today)
        self.assertEqual(addon.ends_at, subscription.ends_at)
        self.assertEqual(school_max_screens(self.school.id), 3)

    def test_screens_purchase_does_not_create_a_second_subscription(self):
        self._activate(ends_at=timezone.localdate() + timedelta(days=181))

        self._start(request_type="screens", extra_screens="1")
        self._pay(MoyasarCheckout.objects.get())

        self.assertEqual(SchoolSubscription.objects.count(), 1)

    def test_screens_purchase_requires_an_active_subscription(self):
        response = self._start(request_type="screens", extra_screens="2")

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertFalse(MoyasarCheckout.objects.exists())

    def test_screens_purchase_requires_a_positive_count(self):
        self._activate(ends_at=timezone.localdate() + timedelta(days=181))

        response = self._start(request_type="screens", extra_screens="0")

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertFalse(MoyasarCheckout.objects.exists())

    # ---- integrity --------------------------------------------------------
    def test_tampered_screen_count_cannot_inflate_the_charge(self):
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="100000")

        checkout = MoyasarCheckout.objects.get()
        self.assertEqual(checkout.extra_screens, MAX_EXTRA_SCREENS)
        self.assertEqual(
            checkout.amount,
            checkout_total(self.plan, MAX_EXTRA_SCREENS),
        )

    def test_changing_screen_count_starts_a_separate_checkout(self):
        """Reusing a recent order must not bill the wrong screen count."""
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="1")
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="4")

        self.assertEqual(MoyasarCheckout.objects.count(), 2)
        self.assertEqual(
            set(MoyasarCheckout.objects.values_list("extra_screens", flat=True)),
            {1, 4},
        )

    def test_identical_request_reuses_the_pending_checkout(self):
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="2")
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="2")

        self.assertEqual(MoyasarCheckout.objects.count(), 1)

    # ---- the customer-facing entry point ----------------------------------
    def test_subscription_page_offers_the_purchase(self):
        self._activate(ends_at=timezone.localdate() + timedelta(days=181))

        response = self.client.get(reverse("dashboard:my_subscription"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تحتاج شاشة إضافية؟")
        self.assertContains(response, 'name="extra_screens"')
        self.assertContains(response, 'value="screens"')

    def test_purchase_is_hidden_without_an_active_subscription(self):
        response = self.client.get(reverse("dashboard:my_subscription"))

        self.assertNotContains(response, "تحتاج شاشة إضافية؟")

    def test_screens_are_granted_once_even_if_payment_is_applied_twice(self):
        self._start(request_type="new", plan_id=self.plan.pk, extra_screens="2")
        checkout = MoyasarCheckout.objects.get()

        self._pay(checkout)
        self._pay(checkout)

        self.assertEqual(SubscriptionScreenAddon.objects.count(), 1)
        self.assertEqual(school_max_screens(self.school.id), 4)

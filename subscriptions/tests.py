from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from subscriptions.models import (
    SchoolSubscription,
    SubscriptionPaymentOperation,
    SubscriptionScreenAddon,
)
from subscriptions.utils import school_effective_max_screens, school_has_active_subscription


class SubscriptionBusinessRulesTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.school = School.objects.create(name="مدرسة الاشتراك", slug="subscription-school")
        self.plan = SubscriptionPlan.objects.create(
            code="professional",
            name="الاحترافية",
            price=Decimal("500.00"),
            duration_days=365,
            max_screens=3,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=self.today - timedelta(days=10),
            ends_at=self.today + timedelta(days=30),
            status="active",
        )

    def test_active_subscription_includes_both_date_boundaries(self):
        self.subscription.starts_at = self.today
        self.subscription.ends_at = self.today
        self.subscription.save(update_fields=["starts_at", "ends_at"])

        self.assertTrue(school_has_active_subscription(self.school.pk, on_date=self.today))

    def test_paid_screen_addons_extend_limit_but_pending_addons_do_not(self):
        SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=2,
            status="paid",
            starts_at=self.today,
            ends_at=self.today + timedelta(days=10),
        )
        SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=20,
            status="pending",
            starts_at=self.today,
            ends_at=self.today + timedelta(days=10),
        )

        self.assertEqual(school_effective_max_screens(self.school.pk, on_date=self.today), 5)

    def test_expired_addon_does_not_extend_the_screen_limit(self):
        SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=2,
            status="paid",
            starts_at=self.today - timedelta(days=20),
            ends_at=self.today - timedelta(days=1),
        )

        self.assertEqual(school_effective_max_screens(self.school.pk, on_date=self.today), 3)

    def test_payment_operation_creates_an_immutable_invoice_snapshot(self):
        operation = SubscriptionPaymentOperation.objects.create(
            school=self.school,
            subscription=self.subscription,
            plan=self.plan,
            amount=Decimal("500.00"),
            method="bank_transfer",
            source="admin_manual",
        )

        invoice = operation.invoice
        original_snapshot = invoice.html_snapshot
        self.school.name = "اسم المدرسة بعد إصدار الفاتورة"
        self.school.save(update_fields=["name"])
        invoice.refresh_from_db()

        self.assertTrue(invoice.invoice_number.startswith("INV-"))
        self.assertEqual(invoice.amount, Decimal("500.00"))
        self.assertEqual(invoice.school, self.school)
        self.assertIn("مدرسة الاشتراك", invoice.html_snapshot)
        self.assertEqual(invoice.html_snapshot, original_snapshot)


class InvoiceTenantIsolationTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.school_a = School.objects.create(name="المدرسة أ", slug="school-a")
        self.school_b = School.objects.create(name="المدرسة ب", slug="school-b")
        plan = SubscriptionPlan.objects.create(code="invoice-plan", name="فاتورة", price=100, max_screens=2)
        self.subscription_a = SchoolSubscription.objects.create(
            school=self.school_a,
            plan=plan,
            starts_at=today,
            ends_at=today + timedelta(days=30),
            status="active",
        )
        subscription_b = SchoolSubscription.objects.create(
            school=self.school_b,
            plan=plan,
            starts_at=today,
            ends_at=today + timedelta(days=30),
            status="active",
        )
        operation_b = SubscriptionPaymentOperation.objects.create(
            school=self.school_b,
            subscription=subscription_b,
            plan=plan,
            amount=100,
            method="bank_transfer",
        )
        self.invoice_b = operation_b.invoice

        self.user = get_user_model().objects.create_user(username="school_a_manager", password="StrongPass123!")
        profile = UserProfile.objects.create(user=self.user, active_school=self.school_a)
        profile.schools.add(self.school_a)

    def test_school_cannot_open_another_schools_invoice_by_guessing_id(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("dashboard:subscription_invoice_view", kwargs={"pk": self.invoice_b.pk})
        )

        self.assertEqual(response.status_code, 404)

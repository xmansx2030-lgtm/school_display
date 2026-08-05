"""Tests for the refund flow and the billing audit trail."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile

from .access import school_subscription_is_active
from .audit import record as audit_record
from .models import (
    SchoolSubscription,
    SubscriptionAuditLog,
    SubscriptionEmailNotification,
    SubscriptionPaymentOperation,
    SubscriptionRefund,
)
from .refunds import record_refund, refundable_amount, refunded_total


class RefundTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة الاسترداد", slug="refund-school")
        self.plan = SubscriptionPlan.objects.create(
            code="refund-plan",
            name="خطة الاسترداد",
            price=Decimal("500.00"),
            duration_days=365,
            max_screens=3,
        )
        self.admin = get_user_model().objects.create_superuser(
            username="refund_admin",
            password="StrongPass123!",
            email="admin@example.com",
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )
        self.operation = SubscriptionPaymentOperation.objects.create(
            school=self.school,
            subscription=self.subscription,
            plan=self.plan,
            amount=Decimal("500.00"),
            method="moyasar",
            source="request",
        )

    def test_full_refund_is_recorded(self):
        refund = record_refund(
            self.operation,
            amount=Decimal("500.00"),
            reason="service_issue",
            actor=self.admin,
        )

        self.assertEqual(refund.status, "completed")
        self.assertIsNotNone(refund.completed_at)
        self.assertEqual(refunded_total(self.operation), Decimal("500.00"))
        self.assertEqual(refundable_amount(self.operation), Decimal("0"))

    def test_partial_refund_leaves_remainder_refundable(self):
        record_refund(self.operation, amount=Decimal("150.00"), actor=self.admin)

        self.assertEqual(refundable_amount(self.operation), Decimal("350.00"))

    def test_cannot_refund_more_than_was_paid(self):
        record_refund(self.operation, amount=Decimal("400.00"), actor=self.admin)

        with self.assertRaises(ValidationError):
            record_refund(self.operation, amount=Decimal("200.00"), actor=self.admin)

        self.assertEqual(refunded_total(self.operation), Decimal("400.00"))

    def test_zero_or_negative_refund_is_rejected(self):
        with self.assertRaises(ValidationError):
            record_refund(self.operation, amount=Decimal("0.00"), actor=self.admin)

    def test_failed_refunds_do_not_consume_refundable_balance(self):
        record_refund(
            self.operation,
            amount=Decimal("500.00"),
            status="failed",
            actor=self.admin,
        )
        self.assertEqual(refundable_amount(self.operation), Decimal("500.00"))

    def test_refund_without_revocation_keeps_access(self):
        record_refund(self.operation, amount=Decimal("100.00"), actor=self.admin)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "active")
        self.assertTrue(school_subscription_is_active(self.school.id))

    def test_refund_with_revocation_cancels_access(self):
        # Warm the cache so we also prove it gets invalidated.
        self.assertTrue(school_subscription_is_active(self.school.id))

        record_refund(
            self.operation,
            amount=Decimal("500.00"),
            reason="cancelled_early",
            revoke_access=True,
            actor=self.admin,
        )

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, "cancelled")
        self.assertIsNotNone(self.subscription.closed_at)
        self.assertFalse(school_subscription_is_active(self.school.id))

    def test_refund_writes_an_audit_entry(self):
        record_refund(
            self.operation,
            amount=Decimal("500.00"),
            reason="duplicate",
            actor=self.admin,
        )

        entry = SubscriptionAuditLog.objects.filter(action="refund_completed").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.school, self.school)
        self.assertEqual(entry.amount, Decimal("500.00"))
        self.assertEqual(entry.actor, self.admin)
        self.assertEqual(entry.context["operation_id"], self.operation.pk)

    def test_rejected_refund_is_not_persisted(self):
        with self.assertRaises(ValidationError):
            record_refund(self.operation, amount=Decimal("900.00"), actor=self.admin)

        self.assertEqual(SubscriptionRefund.objects.count(), 0)


class AuditLogTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة التدقيق", slug="audit-school")

    def test_actor_label_is_denormalised(self):
        user = get_user_model().objects.create_user(
            username="auditor",
            password="StrongPass123!",
            first_name="منصور",
            last_name="الغامدي",
        )
        entry = audit_record("subscription_created", school=self.school, actor=user)

        self.assertEqual(entry.actor_label, "منصور الغامدي")

    def test_system_actions_are_labelled(self):
        entry = audit_record("subscription_expired", school=self.school)
        self.assertEqual(entry.actor_label, "system")

    def test_audit_never_raises_on_bad_input(self):
        # A broken audit write must not take the business action down with it.
        self.assertIsNone(audit_record("subscription_created", school="not-a-school"))

    def test_client_ip_is_captured_from_request(self):
        request = self.client.request().wsgi_request
        request.META["HTTP_X_FORWARDED_FOR"] = "203.0.113.9, 10.0.0.1"

        entry = audit_record("payment_recorded", school=self.school, request=request)

        self.assertEqual(entry.ip_address, "203.0.113.9")


class ResendVerificationTests(TestCase):
    """The resend path is the customer's only self-service escape hatch."""

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة الإرسال", slug="resend-school")
        self.plan = SubscriptionPlan.objects.create(
            code="resend-plan",
            name="خطة الإرسال",
            price=Decimal("300.00"),
            duration_days=365,
            max_screens=2,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )
        self.user = get_user_model().objects.create_user(
            username="resend_manager",
            password="StrongPass123!",
            email="manager@example.com",
        )
        self.profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        self.profile.schools.add(self.school)
        self.client.force_login(self.user)

    def _post(self):
        return self.client.post(reverse("subscriptions:resend_email_verification"))

    def test_queues_a_verification_message(self):
        response = self._post()

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertTrue(
            SubscriptionEmailNotification.objects.filter(
                event_type=SubscriptionEmailNotification.EventType.VERIFY_EMAIL,
                recipient="manager@example.com",
            ).exists()
        )

    def test_is_rate_limited(self):
        self._post()
        self._post()

        # The cooldown must not create a second queued message.
        self.assertEqual(
            SubscriptionEmailNotification.objects.filter(
                event_type=SubscriptionEmailNotification.EventType.VERIFY_EMAIL
            ).count(),
            1,
        )

    def test_already_verified_account_is_told_so(self):
        self.profile.email_verified_at = timezone.now()
        self.profile.save(update_fields=["email_verified_at"])

        response = self._post()

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertFalse(SubscriptionEmailNotification.objects.exists())

    def test_account_without_an_email_is_rejected(self):
        self.user.email = ""
        self.user.save(update_fields=["email"])

        response = self._post()

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertFalse(SubscriptionEmailNotification.objects.exists())

    def test_requires_login(self):
        self.client.logout()
        response = self._post()
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(reverse("dashboard:my_subscription"), response["Location"])

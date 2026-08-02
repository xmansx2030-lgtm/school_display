from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile

from .models import MoyasarCheckout, SubscriptionInvoice, SubscriptionPaymentOperation
from .moyasar_processing import MoyasarVerificationError, apply_payment_details


@override_settings(
    DEBUG=True,
    MOYASAR_ENABLED=True,
    MOYASAR_LIVE_MODE=False,
    MOYASAR_ACTIVATE_TEST_PAYMENTS=False,
    MOYASAR_API_BASE_URL="https://api.moyasar.com/v1",
    MOYASAR_PUBLISHABLE_KEY="pk_test_publishable",
    MOYASAR_SECRET_KEY="sk_test_secret",
    MOYASAR_WEBHOOK_SECRET="webhook-test-secret",
    MOYASAR_CALLBACK_BASE_URL="https://school-display.com",
    MOYASAR_HTTP_TIMEOUT_SECONDS=5,
)
class MoyasarCheckoutTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة ميسر", slug="moyasar-school")
        self.plan = SubscriptionPlan.objects.create(
            code="moyasar-plan",
            name="الخطة السنوية",
            price=Decimal("500.00"),
            duration_days=365,
            max_screens=3,
        )
        self.owner = get_user_model().objects.create_superuser(
            username="moyasar_owner",
            password="StrongPass123!",
            email="owner@example.com",
        )
        owner_profile = UserProfile.objects.create(user=self.owner, active_school=self.school)
        owner_profile.schools.add(self.school)
        self.manager = get_user_model().objects.create_user(
            username="moyasar_manager",
            password="StrongPass123!",
            email="manager@example.com",
        )
        manager_profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        manager_profile.schools.add(self.school)

    def _checkout(self, *, live_mode: bool = False, created_by=None) -> MoyasarCheckout:
        return MoyasarCheckout.objects.create(
            school=self.school,
            created_by=created_by or self.owner,
            plan=self.plan,
            request_type="new",
            starts_at=timezone.localdate(),
            amount=self.plan.price,
            currency="SAR",
            live_mode=live_mode,
        )

    def _details(self, checkout: MoyasarCheckout, *, status: str = "paid", amount: int = 50000):
        return {
            "id": "79cced57-9deb-4c4b-8f48-59c124f79688",
            "status": status,
            "amount": amount,
            "currency": "SAR",
            "live": checkout.live_mode,
            "metadata": {"merchant_reference": checkout.merchant_reference},
        }

    @override_settings(DEBUG=False)
    def test_test_checkout_is_restricted_to_superuser(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("subscriptions:moyasar_start"),
            {"request_type": "new", "plan_id": self.plan.pk},
        )

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertFalse(MoyasarCheckout.objects.exists())

    def test_start_creates_test_checkout_and_never_exposes_secret_key(self):
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("subscriptions:moyasar_start"),
            {"request_type": "new", "plan_id": self.plan.pk},
        )

        checkout = MoyasarCheckout.objects.get()
        self.assertRedirects(
            response,
            reverse("subscriptions:moyasar_checkout", kwargs={"reference": checkout.merchant_reference}),
        )
        page = self.client.get(response.url)
        self.assertContains(page, "pk_test_publishable")
        self.assertNotContains(page, "sk_test_secret")
        self.assertContains(page, "بيئة اختبار")

    def test_paid_test_payment_does_not_activate_real_subscription(self):
        checkout = self._checkout(live_mode=False)

        result = apply_payment_details(checkout.pk, self._details(checkout), event_type="return")

        self.assertEqual(result.status, "paid")
        self.assertIsNone(result.subscription_id)
        self.assertIsNone(result.payment_operation_id)

    @override_settings(
        MOYASAR_LIVE_MODE=True,
        MOYASAR_PUBLISHABLE_KEY="pk_live_publishable",
        MOYASAR_SECRET_KEY="sk_live_secret",
    )
    @patch("subscriptions.moyasar.MoyasarClient.fetch_payment")
    def test_return_verifies_and_activates_live_payment_once(self, fetch_payment):
        checkout = self._checkout(live_mode=True, created_by=self.manager)
        details = self._details(checkout)
        fetch_payment.return_value = details
        self.client.force_login(self.manager)
        return_url = reverse("subscriptions:moyasar_return")

        first = self.client.get(
            return_url,
            {"reference": checkout.merchant_reference, "id": details["id"]},
        )
        second = self.client.get(
            return_url,
            {"reference": checkout.merchant_reference, "id": details["id"]},
        )

        self.assertRedirects(first, reverse("dashboard:my_subscription"))
        self.assertRedirects(second, reverse("dashboard:my_subscription"))
        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "paid")
        self.assertIsNotNone(checkout.subscription_id)
        self.assertEqual(SubscriptionPaymentOperation.objects.filter(method="moyasar").count(), 1)
        self.assertTrue(
            SubscriptionInvoice.objects.filter(operation_id=checkout.payment_operation_id).exists()
        )

    def test_amount_mismatch_is_rejected(self):
        checkout = self._checkout()

        with self.assertRaises(MoyasarVerificationError):
            apply_payment_details(
                checkout.pk,
                self._details(checkout, amount=49999),
                event_type="return",
            )

        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "initiated")
        self.assertIsNone(checkout.subscription_id)

    def test_webhook_rejects_invalid_secret(self):
        response = self.client.post(
            reverse("subscriptions:moyasar_webhook"),
            data=json.dumps({"secret_token": "wrong", "data": {}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(
        MOYASAR_LIVE_MODE=True,
        MOYASAR_PUBLISHABLE_KEY="pk_live_publishable",
        MOYASAR_SECRET_KEY="sk_live_secret",
    )
    def test_signed_webhook_is_idempotent(self):
        checkout = self._checkout(live_mode=True)
        body = {
            "id": "event-1",
            "type": "payment_paid",
            "secret_token": "webhook-test-secret",
            "live": True,
            "data": self._details(checkout),
        }

        first = self.client.post(
            reverse("subscriptions:moyasar_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )
        second = self.client.post(
            reverse("subscriptions:moyasar_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        checkout.refresh_from_db()
        self.assertIsNotNone(checkout.payment_operation_id)
        self.assertEqual(SubscriptionPaymentOperation.objects.filter(method="moyasar").count(), 1)

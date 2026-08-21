import json
from decimal import Decimal
from unittest.mock import patch

import jwt
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from subscriptions.models import (
    SchoolSubscription,
    SubscriptionInvoice,
    SubscriptionPaymentOperation,
    TamaraCheckout,
)


TAMARA_TEST_SETTINGS = {
    "TAMARA_ENABLED": True,
    "TAMARA_API_BASE_URL": "https://api-sandbox.tamara.co",
    "TAMARA_API_TOKEN": "test-api-token",
    "TAMARA_NOTIFICATION_TOKEN": "test-notification-secret-at-least-32-bytes",
    "TAMARA_CALLBACK_BASE_URL": "https://school-display.com",
    "TAMARA_HTTP_TIMEOUT_SECONDS": 5,
    "TAMARA_ELIGIBILITY_TIMEOUT_SECONDS": 0.2,
    "TAMARA_CAPTURE_DIGITAL_ORDERS": True,
    "TAMARA_RECONCILIATION_BATCH_SIZE": 20,
}


@override_settings(**TAMARA_TEST_SETTINGS)
class TamaraCheckoutTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة تمارا", slug="tamara-school")
        self.plan = SubscriptionPlan.objects.create(
            code="tamara-plan",
            name="باقة تمارا",
            price=Decimal("500.00"),
            duration_days=365,
            max_screens=2,
        )
        self.user = get_user_model().objects.create_user(
            username="tamara_manager",
            password="StrongPass123!",
            email="manager@example.com",
            first_name="منصور",
            last_name="الغامدي",
        )
        profile = UserProfile.objects.create(
            user=self.user,
            active_school=self.school,
            mobile="0501234567",
            email_verified_at=timezone.now(),
        )
        profile.schools.add(self.school)
        self.client.force_login(self.user)

    @patch("subscriptions.tamara.TamaraClient.create_checkout")
    @patch("subscriptions.tamara.TamaraClient.is_eligible", return_value=True)
    def test_start_checkout_stores_remote_ids_and_redirects(self, _eligible, create_checkout):
        create_checkout.return_value = {
            "order_id": "order-123",
            "checkout_id": "checkout-123",
            "checkout_url": "https://checkout.tamara.co/session/123",
        }

        response = self.client.post(
            reverse("subscriptions:tamara_start"),
            {"request_type": "new", "plan_id": self.plan.pk},
        )

        self.assertRedirects(
            response,
            "https://checkout.tamara.co/session/123",
            fetch_redirect_response=False,
        )
        checkout = TamaraCheckout.objects.get()
        self.assertEqual(checkout.tamara_order_id, "order-123")
        self.assertEqual(checkout.checkout_id, "checkout-123")
        self.assertEqual(checkout.status, "new")

    @patch("subscriptions.tamara_processing.TamaraClient.capture_order")
    @patch("subscriptions.tamara_processing.TamaraClient.authorise_order")
    @patch("subscriptions.tamara_processing.TamaraClient.get_order")
    def test_approved_webhook_verifies_activates_and_captures(
        self,
        get_order,
        authorise_order,
        capture_order,
    ):
        checkout = TamaraCheckout.objects.create(
            school=self.school,
            created_by=self.user,
            plan=self.plan,
            request_type="new",
            starts_at=timezone.localdate(),
            amount=Decimal("500.00"),
            tamara_order_id="order-456",
            checkout_id="checkout-456",
            checkout_url="https://checkout.tamara.co/session/456",
            status="new",
        )
        get_order.side_effect = [
            {
                "order_id": "order-456",
                "order_reference_id": checkout.merchant_reference,
                "total_amount": {"amount": 500, "currency": "SAR"},
                "status": "approved",
            },
            {
                "order_id": "order-456",
                "order_reference_id": checkout.merchant_reference,
                "total_amount": {"amount": 500, "currency": "SAR"},
                "status": "authorised",
            },
            {
                "order_id": "order-456",
                "order_reference_id": checkout.merchant_reference,
                "total_amount": {"amount": 500, "currency": "SAR"},
                "status": "fully_captured",
            },
        ]
        authorise_order.return_value = {"status": "authorised"}
        capture_order.return_value = {"status": "fully_captured"}
        token = jwt.encode(
            {"iss": "Tamara", "iat": int(timezone.now().timestamp()), "exp": int(timezone.now().timestamp()) + 300},
            TAMARA_TEST_SETTINGS["TAMARA_NOTIFICATION_TOKEN"],
            algorithm="HS256",
        )

        response = self.client.post(
            reverse("subscriptions:tamara_webhook"),
            data=json.dumps(
                {
                    "order_id": "order-456",
                    "order_reference_id": checkout.merchant_reference,
                    "order_number": checkout.merchant_reference,
                    "event_type": "order_approved",
                    "data": [],
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

        self.assertEqual(response.status_code, 200)
        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "captured")
        self.assertIsNotNone(checkout.subscription_id)
        self.assertIsNotNone(checkout.payment_operation_id)
        self.assertTrue(
            SubscriptionPaymentOperation.objects.filter(
                pk=checkout.payment_operation_id,
                method="tamara",
            ).exists()
        )
        self.assertTrue(SubscriptionInvoice.objects.filter(operation=checkout.payment_operation).exists())

    @patch("subscriptions.tamara_processing.TamaraClient.get_order")
    def test_amount_mismatch_never_activates(self, get_order):
        checkout = TamaraCheckout.objects.create(
            school=self.school,
            created_by=self.user,
            plan=self.plan,
            request_type="new",
            amount=Decimal("500.00"),
            tamara_order_id="order-mismatch",
            status="new",
        )
        get_order.return_value = {
            "order_id": "order-mismatch",
            "order_reference_id": checkout.merchant_reference,
            "total_amount": {"amount": 1, "currency": "SAR"},
            "status": "fully_captured",
        }

        response = self.client.get(
            reverse("subscriptions:tamara_success"),
            {"reference": checkout.merchant_reference},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SchoolSubscription.objects.filter(school=self.school).exists())
        self.assertFalse(SubscriptionPaymentOperation.objects.filter(method="tamara").exists())

    def test_subscription_page_offers_tamara(self):
        response = self.client.get(reverse("dashboard:my_subscription"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["tamara_available"])
        self.assertContains(response, reverse("subscriptions:tamara_start"), count=1)
        self.assertContains(response, 'id="tamara-plan-id"')

    def test_invalid_webhook_token_is_rejected(self):
        response = self.client.post(
            reverse("subscriptions:tamara_webhook"),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalid",
        )

        self.assertEqual(response.status_code, 403)

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import jwt
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from subscriptions.models import (
    SchoolSubscription,
    SubscriptionInvoice,
    SubscriptionEmailNotification,
    SubscriptionPaymentOperation,
    SubscriptionScreenAddon,
    TamaraCheckout,
)
from dashboard.forms import SchoolSubscriptionForm
from subscriptions.utils import school_effective_max_screens, school_has_active_subscription
from subscriptions.email_notifications import (
    enqueue_expiry_email_reminders,
    process_pending_email_notifications,
)
from subscriptions.invoicing import reconcile_missing_invoices


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

    def test_cancellation_reason_and_timestamp_are_persisted(self):
        self.subscription.status = "cancelled"
        self.subscription.closure_reason = "budget"
        self.subscription.closure_notes = "لم تعتمد الميزانية"
        self.subscription.save(update_fields=["status", "closure_reason", "closure_notes"])

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.closure_reason, "budget")
        self.assertIsNotNone(self.subscription.closed_at)

    def test_admin_form_requires_a_closure_reason_for_cancelled_subscription(self):
        form = SchoolSubscriptionForm(
            data={
                "school": self.school.pk,
                "plan": self.plan.pk,
                "starts_at": self.subscription.starts_at,
                "status": "cancelled",
                "closure_reason": "",
                "closure_notes": "",
                "notes": "",
                "payment_method": "",
            },
            instance=self.subscription,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("closure_reason", form.errors)

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


@override_settings(
    TRANSACTIONAL_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
    EMAIL_NOTIFICATION_BATCH_SIZE=20,
    EMAIL_NOTIFICATION_MAX_ATTEMPTS=3,
    EMAIL_SUBSCRIPTION_EXPIRY_DAYS=(7, 1, 0),
    SITE_BASE_URL="https://school-display.com",
)
class SubscriptionEmailNotificationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.school = School.objects.create(name="مدرسة البريد", slug="email-school")
        self.plan = SubscriptionPlan.objects.create(
            code="email-plan",
            name="الخطة البريدية",
            price=Decimal("500.00"),
            duration_days=30,
            max_screens=2,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=self.today,
            ends_at=self.today + timedelta(days=7),
            status="active",
        )
        self.user = get_user_model().objects.create_user(
            username="email_manager",
            email="manager@example.com",
            password="StrongPass123!",
        )
        profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        profile.schools.add(self.school)

    def test_new_invoice_is_queued_and_delivered_with_attachment(self):
        operation = SubscriptionPaymentOperation.objects.create(
            school=self.school,
            subscription=self.subscription,
            plan=self.plan,
            amount=Decimal("500.00"),
            method="bank_transfer",
        )

        notification = SubscriptionEmailNotification.objects.get(
            event_type=SubscriptionEmailNotification.EventType.INVOICE
        )
        self.assertEqual(notification.recipient, "manager@example.com")
        self.assertEqual(notification.invoice, operation.invoice)

        result = process_pending_email_notifications()

        self.assertEqual(result.sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(operation.invoice.invoice_number, mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["manager@example.com"])
        self.assertTrue(
            any(
                attachment[0].startswith("invoice-") and attachment[2] == "text/html"
                for attachment in mail.outbox[0].attachments
            )
        )
        notification.refresh_from_db()
        self.assertEqual(notification.status, SubscriptionEmailNotification.Status.SENT)

    def test_worker_reconciles_a_paid_operation_created_without_an_invoice(self):
        operations = SubscriptionPaymentOperation.objects.bulk_create(
            [
                SubscriptionPaymentOperation(
                    school=self.school,
                    subscription=self.subscription,
                    plan=self.plan,
                    amount=Decimal("500.00"),
                    method="bank_transfer",
                )
            ]
        )
        operation = operations[0]
        self.assertFalse(SubscriptionInvoice.objects.filter(operation=operation).exists())

        created = reconcile_missing_invoices()

        self.assertEqual(created, 1)
        invoice = SubscriptionInvoice.objects.get(operation=operation)
        self.assertTrue(invoice.html_snapshot)
        self.assertTrue(
            SubscriptionEmailNotification.objects.filter(
                invoice=invoice,
                recipient="manager@example.com",
            ).exists()
        )

    def test_expiry_reminder_is_deduplicated_and_delivered(self):
        first_count = enqueue_expiry_email_reminders(on_date=self.today)
        second_count = enqueue_expiry_email_reminders(on_date=self.today)

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        notification = SubscriptionEmailNotification.objects.get(
            event_type=SubscriptionEmailNotification.EventType.EXPIRY
        )
        self.assertEqual(notification.reminder_days, 7)

        result = process_pending_email_notifications()

        self.assertEqual(result.sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("متبقي 7 أيام", mail.outbox[0].subject)


@override_settings(
    TAMARA_ENABLED=True,
    TAMARA_API_BASE_URL="https://api-sandbox.tamara.co",
    TAMARA_API_TOKEN="test-api-token",
    TAMARA_NOTIFICATION_TOKEN="test-notification-secret-at-least-32-bytes",
    TAMARA_CALLBACK_BASE_URL="https://school-display.com",
    TAMARA_HTTP_TIMEOUT_SECONDS=5,
)
class TamaraCheckoutTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة تمارا", slug="tamara-school")
        self.plan = SubscriptionPlan.objects.create(
            code="tamara-plan",
            name="الخطة السنوية",
            price=Decimal("600.00"),
            duration_days=365,
            max_screens=3,
        )
        self.user = get_user_model().objects.create_user(
            username="tamara_manager",
            password="StrongPass123!",
            email="manager@example.com",
            first_name="Mona",
            last_name="Customer",
        )
        profile = UserProfile.objects.create(
            user=self.user,
            active_school=self.school,
            mobile="0502223333",
        )
        profile.schools.add(self.school)
        self.client.force_login(self.user)

    @patch("subscriptions.tamara.TamaraClient.create_checkout")
    @patch("subscriptions.tamara.TamaraClient.is_eligible", return_value=True)
    def test_start_checkout_stores_tamara_ids_and_redirects(self, is_eligible, create_checkout):
        create_checkout.return_value = {
            "order_id": "order-123",
            "checkout_id": "checkout-123",
            "checkout_url": "https://checkout.tamara.co/session/123",
            "status": "new",
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
        self.assertEqual(checkout.school, self.school)
        self.assertEqual(checkout.plan, self.plan)
        self.assertEqual(checkout.status, "new")
        self.assertEqual(checkout.tamara_order_id, "order-123")
        payload = create_checkout.call_args.args[0]
        self.assertEqual(payload["total_amount"], {"amount": 600.0, "currency": "SAR"})
        self.assertEqual(payload["consumer"]["phone_number"], "502223333")
        self.assertNotIn("test-api-token", json.dumps(payload))
        is_eligible.assert_called_once_with(
            amount=Decimal("600.00"),
            phone_number="502223333",
            email="manager@example.com",
        )

    @patch("subscriptions.tamara.TamaraClient.create_checkout")
    @patch("subscriptions.tamara.TamaraClient.is_eligible", return_value=False)
    def test_ineligible_checkout_is_declined_before_session_creation(
        self,
        is_eligible,
        create_checkout,
    ):
        response = self.client.post(
            reverse("subscriptions:tamara_start"),
            {"request_type": "new", "plan_id": self.plan.pk},
        )

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        checkout = TamaraCheckout.objects.get()
        self.assertEqual(checkout.status, "declined")
        self.assertEqual(checkout.last_event, "pre_checkout_ineligible")
        is_eligible.assert_called_once()
        create_checkout.assert_not_called()

    def test_subscription_page_renders_new_and_renewal_tamara_actions(self):
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )

        response = self.client.get(reverse("dashboard:my_subscription"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "متابعة الدفع عبر تمارا", count=2)
        self.assertContains(response, reverse("subscriptions:tamara_start"), count=2)
        self.assertContains(response, 'id="tamara-plan-id"')

    def test_public_subscriptions_page_advertises_tamara_when_ready(self):
        response = self.client.get(reverse("website:subscriptions"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "الدفع بالتقسيط عبر تمارا")
        self.assertContains(response, reverse("dashboard:my_subscription"))

    @override_settings(TAMARA_NOTIFICATION_TOKEN="")
    def test_tamara_remains_available_with_pull_reconciliation_only(self):
        dashboard_response = self.client.get(reverse("dashboard:my_subscription"))
        public_response = self.client.get(reverse("website:subscriptions"))

        self.assertContains(dashboard_response, "الدفع الإلكتروني عبر تمارا")
        self.assertContains(public_response, "الدفع بالتقسيط عبر تمارا")

    def _notification_token(self):
        now = timezone.now()
        return jwt.encode(
            {
                "iss": "Tamara",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(minutes=5)).timestamp()),
            },
            "test-notification-secret-at-least-32-bytes",
            algorithm="HS256",
        )

    @patch("subscriptions.tamara_processing.TamaraClient.capture_order")
    @patch("subscriptions.tamara_processing.TamaraClient.authorise_order")
    @patch("subscriptions.tamara_processing.TamaraClient.get_order")
    def test_approved_webhook_authorises_captures_and_activates_once(
        self,
        get_order,
        authorise_order,
        capture_order,
    ):
        get_order.return_value = {"status": "approved"}
        authorise_order.return_value = {"status": "authorised"}
        capture_order.return_value = {"status": "fully_captured"}
        checkout = TamaraCheckout.objects.create(
            school=self.school,
            created_by=self.user,
            plan=self.plan,
            request_type="new",
            starts_at=timezone.localdate(),
            amount=self.plan.price,
            status="new",
            tamara_order_id="order-456",
            checkout_id="checkout-456",
            checkout_url="https://checkout.tamara.co/session/456",
        )
        body = {
            "order_id": "order-456",
            "order_reference_id": checkout.merchant_reference,
            "order_number": checkout.merchant_reference,
            "event_type": "order_approved",
            "data": [],
        }
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self._notification_token()}"}

        first = self.client.post(
            reverse("subscriptions:tamara_webhook"),
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )
        second = self.client.post(
            reverse("subscriptions:tamara_webhook"),
            data=json.dumps(body),
            content_type="application/json",
            **headers,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "captured")
        self.assertIsNotNone(checkout.subscription_id)
        self.assertIsNotNone(checkout.payment_operation_id)
        self.assertEqual(
            SubscriptionPaymentOperation.objects.filter(method="tamara").count(),
            1,
        )
        self.assertTrue(
            SubscriptionInvoice.objects.filter(operation_id=checkout.payment_operation_id).exists()
        )
        get_order.assert_called_once_with("order-456")
        authorise_order.assert_called_once_with("order-456")
        capture_order.assert_called_once()

    @override_settings(TAMARA_NOTIFICATION_TOKEN="")
    @patch("subscriptions.tamara_processing.TamaraClient.capture_order")
    @patch("subscriptions.tamara_processing.TamaraClient.authorise_order")
    @patch("subscriptions.tamara_processing.TamaraClient.get_order")
    def test_success_return_pulls_status_and_activates_without_webhook_token(
        self,
        get_order,
        authorise_order,
        capture_order,
    ):
        get_order.return_value = {"status": "approved"}
        authorise_order.return_value = {"status": "authorised"}
        capture_order.return_value = {"status": "fully_captured"}
        checkout = TamaraCheckout.objects.create(
            school=self.school,
            created_by=self.user,
            plan=self.plan,
            request_type="new",
            starts_at=timezone.localdate(),
            amount=self.plan.price,
            status="new",
            tamara_order_id="order-return",
            checkout_id="checkout-return",
            checkout_url="https://checkout.tamara.co/session/return",
        )

        response = self.client.get(
            reverse("subscriptions:tamara_success"),
            {"reference": checkout.merchant_reference},
        )

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "captured")
        self.assertIsNotNone(checkout.subscription_id)
        self.assertIsNotNone(checkout.payment_operation_id)
        get_order.assert_called_once_with("order-return")
        authorise_order.assert_called_once_with("order-return")
        capture_order.assert_called_once()

    def test_webhook_rejects_invalid_signature(self):
        response = self.client.post(
            reverse("subscriptions:tamara_webhook"),
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalid-token",
        )

        self.assertEqual(response.status_code, 403)

    @override_settings(TAMARA_NOTIFICATION_TOKEN="")
    def test_webhook_reports_missing_notification_token(self):
        response = self.client.post(
            reverse("subscriptions:tamara_webhook"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)

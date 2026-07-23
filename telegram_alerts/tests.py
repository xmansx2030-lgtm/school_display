from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import School, SubscriptionPlan, SupportTicket
from subscriptions.models import (
    SchoolSubscription,
    SubscriptionRequest,
)

from .client import TelegramAPIError
from .models import TelegramAlert
from .services import (
    enqueue_expiry_reminders,
    process_pending_alerts,
    queue_alert,
)


@override_settings(
    TELEGRAM_ALERTS_ENABLED=True,
    TELEGRAM_BOT_TOKEN="123456:test-token",
    TELEGRAM_ADMIN_CHAT_ID="987654321",
    TELEGRAM_ALERTS_BASE_URL="https://school-display.com",
    TELEGRAM_ALERT_EXPIRY_DAYS=(30, 14, 7, 3, 1, 0),
    TELEGRAM_ALERT_MAX_ATTEMPTS=3,
)
class TelegramAlertTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="telegram-owner",
            password="test-password",
        )
        self.school = School.objects.create(
            name="مدرسة التنبيهات",
            slug="telegram-alert-school",
        )
        self.plan = SubscriptionPlan.objects.create(
            code="telegram-plan",
            name="الخطة الاحترافية",
            price=100,
            duration_days=365,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            ends_at=timezone.localdate() + timedelta(days=7),
            status="active",
        )
        TelegramAlert.objects.all().delete()

    def test_support_ticket_creation_queues_one_escaped_alert(self):
        ticket = SupportTicket.objects.create(
            user=self.user,
            school=self.school,
            subject="<تنبيه عاجل>",
            message="نحتاج مساعدة <script>alert(1)</script>",
            priority="urgent",
        )

        alert = TelegramAlert.objects.get(
            dedupe_key=f"support-ticket-created:{ticket.pk}"
        )
        self.assertEqual(alert.event_type, "support_ticket_created")
        self.assertIn("&lt;تنبيه عاجل&gt;", alert.message)
        self.assertNotIn("<script>", alert.message)
        self.assertIn(f"/dashboard/admin-panel/support/{ticket.pk}/", alert.action_url)

    def test_subscription_request_creation_queues_alert(self):
        request = SubscriptionRequest.objects.create(
            school=self.school,
            created_by=self.user,
            request_type="new",
            plan=self.plan,
            requested_starts_at=timezone.localdate(),
            amount=100,
            receipt_image="receipts/test.pdf",
        )

        alert = TelegramAlert.objects.get(
            dedupe_key=f"subscription-request-created:{request.pk}"
        )
        self.assertIn("طلب اشتراك جديد", alert.message)
        self.assertIn(
            f"/dashboard/admin-panel/subscription-requests/{request.pk}/",
            alert.action_url,
        )

    def test_expiry_reminders_are_deduplicated(self):
        first_count = enqueue_expiry_reminders(on_date=timezone.localdate())
        second_count = enqueue_expiry_reminders(on_date=timezone.localdate())

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        alert = TelegramAlert.objects.get(event_type="subscription_expiry")
        self.assertIn("متبقي 7 يومًا", alert.message)

    def test_queue_alert_uses_unique_dedupe_key(self):
        first, first_created = queue_alert(
            event_type="test",
            dedupe_key="same-event",
            message="الأولى",
        )
        second, second_created = queue_alert(
            event_type="test",
            dedupe_key="same-event",
            message="الثانية",
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(TelegramAlert.objects.get(pk=first.pk).message, "الأولى")

    @patch("telegram_alerts.client.TelegramClient.send_message")
    def test_pending_alert_is_marked_sent(self, send_message):
        send_message.return_value = {"message_id": 12345}
        alert, _ = queue_alert(
            event_type="test",
            dedupe_key="delivery-success",
            message="<b>اختبار</b>",
        )

        result = process_pending_alerts(limit=5)

        alert.refresh_from_db()
        self.assertEqual(result.sent, 1)
        self.assertEqual(alert.status, TelegramAlert.Status.SENT)
        self.assertEqual(alert.telegram_message_id, 12345)
        self.assertIsNotNone(alert.sent_at)

    @patch("telegram_alerts.client.TelegramClient.send_message")
    def test_failed_delivery_is_retried_without_losing_alert(self, send_message):
        send_message.side_effect = TelegramAPIError("temporary failure")
        alert, _ = queue_alert(
            event_type="test",
            dedupe_key="delivery-retry",
            message="اختبار",
        )

        result = process_pending_alerts(limit=5)

        alert.refresh_from_db()
        self.assertEqual(result.retried, 1)
        self.assertEqual(alert.status, TelegramAlert.Status.PENDING)
        self.assertEqual(alert.attempts, 1)
        self.assertIn("temporary failure", alert.last_error)

    @override_settings(TELEGRAM_ALERTS_ENABLED=False)
    def test_disabled_integration_does_not_queue(self):
        alert, created = queue_alert(
            event_type="test",
            dedupe_key="disabled",
            message="لن يُرسل",
        )
        self.assertIsNone(alert)
        self.assertFalse(created)

from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from svix.webhooks import Webhook

from .models import MailMessage, MailWebhookEvent


WEBHOOK_SECRET = "whsec_dGVzdC1yZXNlbmQtc2VjcmV0"


@override_settings(
    RESEND_WEBHOOK_SECRET=WEBHOOK_SECRET,
    RESEND_API_KEY="",
    RESEND_API_BASE_URL="https://api.resend.com",
    RESEND_HTTP_TIMEOUT_SECONDS=2,
)
class ResendWebhookTests(TestCase):
    def _post(self, payload: dict, *, event_id: str = "evt_test_1"):
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        timestamp = timezone.now()
        signature = Webhook(WEBHOOK_SECRET).sign(event_id, timestamp, body)
        return self.client.post(
            reverse("mailcenter:resend_webhook"),
            data=body,
            content_type="application/json",
            HTTP_SVIX_ID=event_id,
            HTTP_SVIX_TIMESTAMP=str(int(timestamp.timestamp())),
            HTTP_SVIX_SIGNATURE=signature,
        )

    def test_rejects_unsigned_payload(self):
        response = self.client.post(
            reverse("mailcenter:resend_webhook"),
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(MailWebhookEvent.objects.exists())

    def test_received_event_is_idempotent_and_creates_unread_inbox_message(self):
        payload = {
            "type": "email.received",
            "created_at": timezone.now().isoformat(),
            "data": {
                "email_id": "email_inbound_1",
                "from": "customer@example.com",
                "to": ["support@mail.school-display.com"],
                "subject": "طلب مساعدة",
                "message_id": "<customer-message@example.com>",
            },
        }
        first = self._post(payload)
        second = self._post(payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(MailMessage.objects.count(), 1)
        self.assertEqual(MailWebhookEvent.objects.count(), 1)
        message = MailMessage.objects.get()
        self.assertEqual(message.direction, MailMessage.Direction.INBOUND)
        self.assertEqual(message.status, MailMessage.Status.RECEIVED)
        self.assertFalse(message.is_read)

    @patch("mailcenter.services.retrieve_email")
    def test_password_reset_body_is_never_stored(self, retrieve_email):
        retrieve_email.return_value = {
            "id": "email_reset_1",
            "from": "no-reply@mail.school-display.com",
            "to": ["manager@example.com"],
            "subject": "استعادة كلمة المرور | لوحة العرض الذكية",
            "text": "secret reset link",
            "html": "<a href='https://example.com/reset/token'>reset</a>",
        }
        payload = {
            "type": "email.sent",
            "created_at": timezone.now().isoformat(),
            "data": {
                "email_id": "email_reset_1",
                "from": "no-reply@mail.school-display.com",
                "to": ["manager@example.com"],
                "subject": "استعادة كلمة المرور | لوحة العرض الذكية",
            },
        }

        response = self._post(payload, event_id="evt_reset")

        self.assertEqual(response.status_code, 200)
        message = MailMessage.objects.get(provider_id="email_reset_1")
        self.assertTrue(message.is_sensitive)
        self.assertEqual(message.text_body, "")
        self.assertEqual(message.html_body, "")
        self.assertIn("حجب", message.preview)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="لوحة العرض الذكية <no-reply@mail.school-display.com>",
    EMAIL_REPLY_TO="دعم لوحة العرض الذكية <support@mail.school-display.com>",
    TWO_FACTOR_REQUIRED_FOR_PRIVILEGED=False,
)
class MailCenterDashboardTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_superuser(
            username="mail_owner",
            email="owner@example.com",
            password="StrongPass123!",
        )
        self.client.force_login(self.owner)

    def test_opening_inbound_message_marks_it_read(self):
        message = MailMessage.objects.create(
            provider_id="inbound-read-test",
            direction=MailMessage.Direction.INBOUND,
            status=MailMessage.Status.RECEIVED,
            from_address="customer@example.com",
            to_addresses=["support@mail.school-display.com"],
            subject="سؤال",
        )

        response = self.client.get(reverse("dashboard:system_mail_detail", args=[message.pk]))

        self.assertEqual(response.status_code, 200)
        message.refresh_from_db()
        self.assertTrue(message.is_read)
        self.assertEqual(message.read_by, self.owner)

    def test_mail_center_list_is_available_to_platform_owner(self):
        response = self.client.get(reverse("dashboard:system_mail_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مركز البريد")
        self.assertContains(response, "support@mail.school-display.com")

    def test_compose_sends_through_django_backend_and_records_outbox(self):
        response = self.client.post(
            reverse("dashboard:system_mail_compose"),
            {
                "recipients": "customer@example.com",
                "subject": "تفاصيل اشتراكك",
                "body": "مرحبًا، هذه تفاصيل اشتراكك.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["customer@example.com"])
        message = MailMessage.objects.get(direction=MailMessage.Direction.OUTBOUND)
        self.assertEqual(message.status, MailMessage.Status.SENT)
        self.assertEqual(message.subject, "تفاصيل اشتراكك")

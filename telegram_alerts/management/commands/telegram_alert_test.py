from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from telegram_alerts.client import TelegramAPIError, TelegramClient
from telegram_alerts.services import configuration_errors


class Command(BaseCommand):
    help = "Validate Telegram credentials and send a private test alert."

    def handle(self, *args, **options):
        errors = configuration_errors()
        if errors:
            raise CommandError("; ".join(errors))

        client = TelegramClient()
        try:
            bot = client.get_me()
            result = client.send_message(
                message=(
                    "<b>✅ تم ربط منصة شاشات المدارس بنجاح</b>\n\n"
                    "سيصلك هنا تنبيه عند وجود اشتراك جديد أو تذكرة دعم "
                    "أو اقتراب انتهاء اشتراك.\n"
                    f"🕒 وقت الاختبار: <code>{timezone.localtime().strftime('%Y-%m-%d %H:%M')}</code>"
                ),
                action_url=str(settings.TELEGRAM_ALERTS_BASE_URL).strip(),
                action_label="فتح المنصة",
            )
        except TelegramAPIError as exc:
            raise CommandError(str(exc)) from None

        username = str(bot.get("username") or "unknown")
        message_id = result.get("message_id")
        self.stdout.write(
            self.style.SUCCESS(
                f"Telegram alert sent successfully via @{username} (message_id={message_id})."
            )
        )

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from telegram_alerts.client import TelegramAPIError, TelegramClient
from telegram_alerts.services import configuration_errors


class Command(BaseCommand):
    help = "List private Telegram chat IDs seen after a user sends /start to the bot."

    def handle(self, *args, **options):
        errors = configuration_errors(require_chat_id=False)
        if errors:
            raise CommandError("; ".join(errors))

        try:
            updates = TelegramClient(chat_id="").get_updates()
        except TelegramAPIError as exc:
            raise CommandError(str(exc)) from None

        chats: dict[str, dict] = {}
        for update in updates:
            message = update.get("message") if isinstance(update, dict) else None
            chat = message.get("chat") if isinstance(message, dict) else None
            if not isinstance(chat, dict) or chat.get("type") != "private":
                continue
            chat_id = str(chat.get("id") or "").strip()
            if chat_id:
                chats[chat_id] = chat

        if not chats:
            self.stdout.write(
                self.style.WARNING(
                    "No private chat found. Open the bot in Telegram, send /start, then run this command again."
                )
            )
            return

        for chat_id, chat in chats.items():
            name = " ".join(
                part
                for part in (
                    str(chat.get("first_name") or "").strip(),
                    str(chat.get("last_name") or "").strip(),
                )
                if part
            )
            username = str(chat.get("username") or "").strip()
            self.stdout.write(
                f"chat_id={chat_id} name={name or '—'} username=@{username or '—'}"
            )

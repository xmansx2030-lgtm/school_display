from __future__ import annotations

from typing import Any

import requests
from django.conf import settings


class TelegramAPIError(RuntimeError):
    """A sanitized Telegram API failure that never exposes the bot token."""


class TelegramClient:
    def __init__(
        self,
        *,
        token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self.token = (token if token is not None else settings.TELEGRAM_BOT_TOKEN).strip()
        self.chat_id = (
            chat_id if chat_id is not None else settings.TELEGRAM_ADMIN_CHAT_ID
        ).strip()
        self.api_base = settings.TELEGRAM_API_BASE_URL.rstrip("/")
        self.timeout = int(settings.TELEGRAM_ALERT_HTTP_TIMEOUT_SECONDS)

    def _sanitized(self, value: object) -> str:
        text = str(value or "").strip()
        if self.token:
            text = text.replace(self.token, "***")
        return text[:1000]

    def _request(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            raise TelegramAPIError("TELEGRAM_BOT_TOKEN is not configured")

        url = f"{self.api_base}/bot{self.token}/{method}"
        try:
            response = requests.post(
                url,
                json=payload or {},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise TelegramAPIError(
                f"Telegram network error ({exc.__class__.__name__})"
            ) from None

        try:
            data = response.json()
        except ValueError:
            raise TelegramAPIError(
                f"Telegram returned invalid JSON (HTTP {response.status_code})"
            ) from None

        if response.status_code >= 400 or not bool(data.get("ok")):
            description = self._sanitized(data.get("description") or "unknown Telegram error")
            raise TelegramAPIError(
                f"Telegram API rejected the request (HTTP {response.status_code}): {description}"
            )

        result = data.get("result")
        return result if isinstance(result, dict) else {"items": result}

    def get_me(self) -> dict[str, Any]:
        return self._request("getMe")

    def get_updates(self) -> list[dict[str, Any]]:
        result = self._request(
            "getUpdates",
            {
                "timeout": 0,
                "limit": 100,
                "allowed_updates": ["message"],
            },
        )
        items = result.get("items")
        return items if isinstance(items, list) else []

    def send_message(
        self,
        *,
        message: str,
        action_url: str = "",
        action_label: str = "",
    ) -> dict[str, Any]:
        if not self.chat_id:
            raise TelegramAPIError("TELEGRAM_ADMIN_CHAT_ID is not configured")

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if action_url.startswith(("https://", "http://")):
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {
                            "text": action_label.strip() or "فتح لوحة التحكم",
                            "url": action_url,
                        }
                    ]
                ]
            }
        return self._request("sendMessage", payload)

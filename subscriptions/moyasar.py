from __future__ import annotations

import logging
from urllib.parse import quote

import requests
from django.conf import settings


logger = logging.getLogger(__name__)

_OFFICIAL_API_BASE = "https://api.moyasar.com/v1"


class MoyasarConfigurationError(RuntimeError):
    pass


class MoyasarAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class MoyasarClient:
    def __init__(self):
        self.base_url = str(getattr(settings, "MOYASAR_API_BASE_URL", "") or "").strip().rstrip("/")
        self.secret_key = str(getattr(settings, "MOYASAR_SECRET_KEY", "") or "").strip()
        self.timeout = int(getattr(settings, "MOYASAR_HTTP_TIMEOUT_SECONDS", 15) or 15)
        if self.base_url != _OFFICIAL_API_BASE:
            raise MoyasarConfigurationError("MOYASAR_API_BASE_URL must be the official Moyasar API URL")
        if not self.secret_key:
            raise MoyasarConfigurationError("Moyasar secret key is not configured")

    def fetch_payment(self, payment_id: str) -> dict:
        safe_payment_id = quote(str(payment_id), safe="")
        try:
            response = requests.get(
                f"{self.base_url}/payments/{safe_payment_id}",
                auth=(self.secret_key, ""),
                headers={"Accept": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MoyasarAPIError("تعذر الاتصال بميسر حاليًا.") from exc

        if response.status_code < 200 or response.status_code >= 300:
            logger.warning(
                "moyasar_api_error action=fetch_payment status=%s",
                response.status_code,
            )
            raise MoyasarAPIError(
                "تعذر التحقق من عملية ميسر. حاول مرة أخرى بعد قليل.",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise MoyasarAPIError(
                "أعادت ميسر استجابة غير صالحة.",
                status_code=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise MoyasarAPIError("أعادت ميسر استجابة غير صالحة.", status_code=response.status_code)
        return data

from __future__ import annotations

import logging
from datetime import timezone as dt_timezone
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone


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

    def _get(self, path: str, *, action: str, params: dict | None = None) -> dict:
        try:
            response = requests.get(
                f"{self.base_url}{path}",
                auth=(self.secret_key, ""),
                headers={"Accept": "application/json"},
                params=params or None,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise MoyasarAPIError("تعذر الاتصال بمزود الدفع الإلكتروني حاليًا.") from exc

        if response.status_code < 200 or response.status_code >= 300:
            logger.warning(
                "moyasar_api_error action=%s status=%s",
                action,
                response.status_code,
            )
            raise MoyasarAPIError(
                "تعذر التحقق من عملية الدفع الإلكتروني. حاول مرة أخرى بعد قليل.",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise MoyasarAPIError(
                "أعاد مزود الدفع استجابة غير صالحة.",
                status_code=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise MoyasarAPIError("أعاد مزود الدفع استجابة غير صالحة.", status_code=response.status_code)
        return data

    def fetch_payment(self, payment_id: str) -> dict:
        safe_payment_id = quote(str(payment_id), safe="")
        return self._get(f"/payments/{safe_payment_id}", action="fetch_payment")

    def list_payments(self, *, created_after=None, page: int = 1, limit: int = 100) -> tuple[list[dict], bool]:
        """Return one page of recent payments plus whether another page exists.

        Reconciliation needs this because a customer who closes the browser
        before the callback leaves us with a paid payment and no local
        ``payment_id`` to look up directly.
        """
        params: dict[str, str] = {
            "page": str(max(1, int(page))),
            "limit": str(max(1, min(100, int(limit)))),
        }
        if created_after is not None:
            # The filter is sent without an offset, so it must be normalised to
            # UTC first. Formatting a localised datetime would shift the window
            # by the offset and hide payments that fall in the gap.
            if timezone.is_aware(created_after):
                created_after = created_after.astimezone(dt_timezone.utc)
            # Moyasar's Payments API documents this filter as ``created[gt]``.
            # An unknown ``created[gte]`` parameter is silently ignored, which
            # can make reconciliation scan the wrong ledger window.
            params["created[gt]"] = created_after.strftime("%Y-%m-%d %H:%M:%S")

        data = self._get("/payments", action="list_payments", params=params)

        raw_payments = data.get("payments")
        payments = [item for item in raw_payments if isinstance(item, dict)] if isinstance(raw_payments, list) else []

        return payments, self._has_next_page(data.get("meta"), requested_page=int(params["page"]))

    @staticmethod
    def _has_next_page(meta: object, *, requested_page: int) -> bool:
        """Whether another page of results exists.

        Callers treat "no next page" as proof the ledger was read to the end,
        and act on that. Reading it wrongly would cut a sweep short, so an
        unrecognised ``meta`` shape reports "more may follow" rather than
        claiming completeness.
        """
        if not isinstance(meta, dict):
            return True

        next_page = meta.get("next_page")
        if isinstance(next_page, int):
            return next_page > requested_page
        if next_page is None and "next_page" in meta:
            return False

        total_pages = meta.get("total_pages")
        current_page = meta.get("current_page")
        if isinstance(total_pages, int) and isinstance(current_page, int):
            return current_page < total_pages

        return True

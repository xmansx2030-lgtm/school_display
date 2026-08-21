from __future__ import annotations

import logging
import re
from decimal import Decimal
from urllib.parse import quote, urlparse

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import TamaraCheckout


logger = logging.getLogger(__name__)

_OFFICIAL_API_BASES = {
    "https://api-sandbox.tamara.co",
    "https://api.tamara.co",
}


class TamaraError(Exception):
    pass


class TamaraConfigurationError(TamaraError):
    pass


class TamaraAPIError(TamaraError):
    def __init__(self, message: str, *, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


def _money(value: Decimal | int | str) -> dict[str, object]:
    return {"amount": float(Decimal(value).quantize(Decimal("0.01"))), "currency": "SAR"}


def _tamara_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("00966"):
        digits = digits[5:]
    elif digits.startswith("966"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"5\d{8}", digits):
        raise TamaraConfigurationError("يلزم إضافة رقم جوال سعودي صحيح إلى الحساب قبل الدفع عبر تمارا.")
    return digits


def _consumer(user) -> dict[str, str]:
    email = (getattr(user, "email", "") or "").strip()
    if not email or "@" not in email:
        raise TamaraConfigurationError("يلزم إضافة بريد إلكتروني صحيح إلى الحساب قبل الدفع عبر تمارا.")

    profile = getattr(user, "profile", None)
    phone = _tamara_phone(getattr(profile, "mobile", "") or "")
    first_name = (getattr(user, "first_name", "") or "").strip() or "School"
    last_name = (getattr(user, "last_name", "") or "").strip() or "Customer"
    return {
        "first_name": first_name[:100],
        "last_name": last_name[:100],
        "phone_number": phone,
        "email": email[:128],
    }


def _callback_url(name: str, reference: str) -> str:
    base = str(getattr(settings, "TAMARA_CALLBACK_BASE_URL", "") or "").rstrip("/")
    if not base:
        raise TamaraConfigurationError("TAMARA_CALLBACK_BASE_URL is not configured")
    return f"{base}{reverse(name)}?reference={reference}"


def build_checkout_payload(checkout: TamaraCheckout, user, *, is_mobile: bool = False) -> dict:
    amount = Decimal(checkout.amount).quantize(Decimal("0.01"))
    if amount <= 0:
        raise TamaraConfigurationError("لا يمكن إنشاء دفعة تمارا لخطة مجانية.")

    plan = checkout.plan
    item_ref = f"plan-{plan.pk}"
    plan_code = (getattr(plan, "code", "") or item_ref)[:128]
    duration_days = int(getattr(plan, "duration_days", 0) or 0)
    ends_at = checkout.starts_at
    if duration_days > 0:
        from datetime import timedelta

        ends_at = checkout.starts_at + timedelta(days=duration_days)

    paid_checkouts = TamaraCheckout.objects.filter(
        created_by=user,
        status__in=["authorised", "captured"],
    ).count()

    return {
        "total_amount": _money(amount),
        "shipping_amount": _money(0),
        "tax_amount": _money(0),
        "order_reference_id": checkout.merchant_reference,
        "order_number": checkout.merchant_reference,
        "consumer": _consumer(user),
        "items": [
            {
                "reference_id": item_ref,
                "type": "Subscription - Digital",
                "name": str(plan.name)[:255],
                "sku": plan_code,
                "quantity": 1,
                "unit_price": _money(amount),
                "tax_amount": _money(0),
                "discount_amount": _money(0),
                "total_amount": _money(amount),
            }
        ],
        "country_code": "SA",
        "description": f"School Display subscription - {plan.name}"[:256],
        "merchant_url": {
            "success": _callback_url("subscriptions:tamara_success", checkout.merchant_reference),
            "failure": _callback_url("subscriptions:tamara_failure", checkout.merchant_reference),
            "cancel": _callback_url("subscriptions:tamara_cancel", checkout.merchant_reference),
        },
        "locale": "ar_SA",
        "platform": "School Display Web",
        "is_mobile": bool(is_mobile),
        "risk_assessment": {
            "is_premium_customer": paid_checkouts > 0,
            "account_creation_date": user.date_joined.strftime("%d-%m-%Y"),
            "total_order_count": paid_checkouts,
            "date_first_paid": None,
            "date_last_paid": None,
            "education": {
                "education_type": "School display subscription",
                "start_date": checkout.starts_at.strftime("%d-%m-%Y"),
                "end_date": ends_at.strftime("%d-%m-%Y"),
                "event_location": "Online",
                "purchase_type": "Subscription",
            },
        },
        "additional_data": {
            "school_id": checkout.school_id,
            "plan_id": checkout.plan_id,
            "request_type": checkout.request_type,
        },
    }


class TamaraClient:
    def __init__(self):
        self.base_url = str(getattr(settings, "TAMARA_API_BASE_URL", "") or "").rstrip("/")
        self.api_token = str(getattr(settings, "TAMARA_API_TOKEN", "") or "").strip()
        self.timeout = int(getattr(settings, "TAMARA_HTTP_TIMEOUT_SECONDS", 15) or 15)
        if self.base_url not in _OFFICIAL_API_BASES:
            raise TamaraConfigurationError("TAMARA_API_BASE_URL must be an official Tamara API URL")
        if not self.api_token:
            raise TamaraConfigurationError("Tamara API token is not configured")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                headers=self.headers,
                timeout=self.timeout if timeout is None else timeout,
            )
        except requests.RequestException as exc:
            raise TamaraAPIError("تعذر الاتصال بتمارا حاليًا.") from exc

        if response.status_code < 200 or response.status_code >= 300:
            logger.warning(
                "tamara_api_error method=%s path=%s status=%s",
                method,
                path,
                response.status_code,
            )
            raise TamaraAPIError(
                "رفضت تمارا الطلب. تحقق من بيانات الحساب والخطة ثم أعد المحاولة.",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise TamaraAPIError("أعادت تمارا استجابة غير صالحة.", status_code=response.status_code) from exc
        if not isinstance(data, dict):
            raise TamaraAPIError("أعادت تمارا استجابة غير صالحة.", status_code=response.status_code)
        return data

    def _post(
        self,
        path: str,
        payload: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        return self._request("POST", path, payload, timeout=timeout)

    def _get(self, path: str) -> dict:
        return self._request("GET", path)

    def is_eligible(self, *, amount: Decimal, phone_number: str, email: str) -> bool | None:
        phone = re.sub(r"\D+", "", phone_number or "")
        if phone and not phone.startswith("966"):
            phone = f"966{phone.lstrip('0')}"
        payload = {
            "order": {
                "amount": float(Decimal(amount).quantize(Decimal("0.01"))),
                "currency": "SAR",
            },
            "customer": {
                "phone_number": phone,
                "email": (email or "").strip()[:128],
            },
        }
        try:
            data = self._post(
                "/pre-checkout/v1/eligibility",
                payload,
                timeout=float(getattr(settings, "TAMARA_ELIGIBILITY_TIMEOUT_SECONDS", 0.2)),
            )
        except TamaraAPIError:
            # Tamara explicitly recommends fail-open behavior on timeout/error so
            # eligibility latency does not block checkout.
            return None
        eligible = data.get("is_eligible")
        return eligible if isinstance(eligible, bool) else None

    def create_checkout(self, payload: dict) -> dict:
        data = self._post("/checkout", payload)
        required = ("order_id", "checkout_id", "checkout_url")
        if any(not data.get(key) for key in required):
            raise TamaraAPIError("لم تُرجع تمارا بيانات جلسة الدفع كاملة.")

        parsed = urlparse(str(data["checkout_url"]))
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (hostname == "tamara.co" or hostname.endswith(".tamara.co")):
            raise TamaraAPIError("رابط الدفع المعاد من تمارا غير صالح.")
        return data

    def authorise_order(self, order_id: str) -> dict:
        safe_order_id = quote(str(order_id), safe="")
        return self._post(f"/orders/{safe_order_id}/authorise", {})

    def get_order(self, order_id: str) -> dict:
        safe_order_id = quote(str(order_id), safe="")
        return self._get(f"/orders/{safe_order_id}")

    def capture_order(self, checkout: TamaraCheckout) -> dict:
        amount = Decimal(checkout.amount).quantize(Decimal("0.01"))
        plan = checkout.plan
        plan_code = (getattr(plan, "code", "") or f"plan-{plan.pk}")[:128]
        payload = {
            "order_id": str(checkout.tamara_order_id),
            "total_amount": _money(amount),
            "shipping_info": {
                "shipped_at": timezone.now().isoformat().replace("+00:00", "Z"),
                "shipping_company": "School Display Digital Delivery",
                "tracking_number": checkout.merchant_reference,
                "tracking_url": _callback_url(
                    "subscriptions:tamara_success",
                    checkout.merchant_reference,
                ),
            },
            "items": [
                {
                    "reference_id": f"plan-{plan.pk}",
                    "type": "Digital",
                    "name": str(plan.name)[:255],
                    "sku": plan_code,
                    "quantity": 1,
                    "unit_price": _money(amount),
                    "tax_amount": _money(0),
                    "discount_amount": _money(0),
                    "total_amount": _money(amount),
                }
            ],
            "discount_amount": _money(0),
            "shipping_amount": _money(0),
            "tax_amount": _money(0),
        }
        return self._post("/payments/capture", payload)

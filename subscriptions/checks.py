"""Deployment checks for the commercial (billing) surface.

These run through ``manage.py check --deploy``, which CI already enforces, so a
misconfigured launch fails loudly instead of silently refusing customer money.
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Warning as CheckWarning, register


ID_PREFIX = "subscriptions"


def _production() -> bool:
    return not getattr(settings, "DEBUG", False) and not getattr(settings, "RUNNING_TESTS", False)


@register("subscriptions")
def check_payment_configuration(app_configs, **kwargs):
    issues = []
    if not _production():
        return issues

    moyasar_enabled = bool(getattr(settings, "MOYASAR_ENABLED", False))

    if moyasar_enabled:
        if not getattr(settings, "MOYASAR_LIVE_MODE", False):
            issues.append(
                CheckWarning(
                    "Moyasar is enabled but still running in test mode.",
                    hint=(
                        "Customers cannot pay: checkout is restricted to superusers while "
                        "MOYASAR_LIVE_MODE is False. Set MOYASAR_LIVE_MODE=True with live keys "
                        "before opening sales."
                    ),
                    id=f"{ID_PREFIX}.W001",
                )
            )
        if "googlepay" in list(getattr(settings, "MOYASAR_PAYMENT_METHODS", [])) and not str(
            getattr(settings, "MOYASAR_GOOGLE_PAY_MERCHANT_ID", "") or ""
        ).strip():
            issues.append(
                CheckWarning(
                    "Moyasar is configured to offer Google Pay but the merchant ID is missing.",
                    hint=(
                        "Set MOYASAR_GOOGLE_PAY_MERCHANT_ID so the Moyasar form can render "
                        "the Google Pay button in production."
                    ),
                    id=f"{ID_PREFIX}.W003",
                )
            )
        if not str(getattr(settings, "MOYASAR_WEBHOOK_SECRET", "") or ""):
            issues.append(
                Error(
                    "Moyasar is enabled without a webhook secret.",
                    hint=(
                        "Without MOYASAR_WEBHOOK_SECRET the webhook returns 503 and paid "
                        "subscriptions are only activated by the return URL or the "
                        "reconciliation worker. Configure the secret."
                    ),
                    id=f"{ID_PREFIX}.E001",
                )
            )

    if moyasar_enabled and not getattr(settings, "TRANSACTIONAL_EMAIL_ENABLED", False):
        issues.append(
            CheckWarning(
                "A payment gateway is enabled but transactional email is off.",
                hint=(
                    "Invoices and expiry reminders are queued but never delivered. "
                    "Set TRANSACTIONAL_EMAIL_ENABLED=True and configure SMTP."
                ),
                id=f"{ID_PREFIX}.W002",
            )
        )

    return issues

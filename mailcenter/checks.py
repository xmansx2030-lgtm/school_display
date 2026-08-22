from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Warning as CheckWarning, register


@register("mailcenter")
def check_mail_configuration(app_configs, **kwargs):
    if getattr(settings, "DEBUG", False) or getattr(settings, "RUNNING_TESTS", False):
        return []
    issues = []
    if not getattr(settings, "TRANSACTIONAL_EMAIL_ENABLED", False):
        return issues

    backend = str(getattr(settings, "EMAIL_BACKEND", "") or "")
    if not backend.endswith("smtp.EmailBackend"):
        issues.append(
            Error(
                "Transactional email is enabled without the SMTP backend.",
                hint="Use django.core.mail.backends.smtp.EmailBackend with Resend SMTP.",
                id="mailcenter.E001",
            )
        )
    from_email = str(getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").casefold()
    if "@mail.school-display.com" not in from_email:
        issues.append(
            CheckWarning(
                "The sender is not using the isolated verified mail subdomain.",
                hint="Set DEFAULT_FROM_EMAIL to no-reply@mail.school-display.com.",
                id="mailcenter.W001",
            )
        )
    email_host = str(getattr(settings, "EMAIL_HOST", "") or "").casefold()
    resend_events_expected = (
        "resend.com" in email_host or getattr(settings, "RESEND_INBOUND_ENABLED", False)
    )
    if resend_events_expected and not str(
        getattr(settings, "RESEND_WEBHOOK_SECRET", "") or ""
    ).strip():
        issues.append(
            Error(
                "Resend delivery events are enabled without a webhook signing secret.",
                hint="Set RESEND_WEBHOOK_SECRET or RESEND_WEBHOOK_SECRET_FILE.",
                id="mailcenter.E002",
            )
        )
    if getattr(settings, "RESEND_INBOUND_ENABLED", False) and not str(
        getattr(settings, "RESEND_API_KEY", "") or ""
    ).strip():
        issues.append(
            Error(
                "Inbound email is enabled without a Resend API key.",
                hint="Set a separate full-access RESEND_API_KEY for retrieving inbound content.",
                id="mailcenter.E003",
            )
        )
    return issues

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from core.email_verification import max_age_seconds

from .models import (
    SchoolSubscription,
    SubscriptionEmailNotification,
    SubscriptionInvoice,
)

logger = logging.getLogger(__name__)

WORKER_HEARTBEAT_KEY = "subscriptions:email-worker:heartbeat"
WORKER_HEARTBEAT_TTL_SECONDS = 180


@dataclass(frozen=True)
class DeliveryResult:
    sent: int = 0
    skipped: int = 0
    retried: int = 0
    failed: int = 0


def email_notifications_enabled() -> bool:
    return bool(getattr(settings, "TRANSACTIONAL_EMAIL_ENABLED", False))


def _school_recipient_emails(subscription: SchoolSubscription) -> list[str]:
    profiles = (
        subscription.school.users.select_related("user")
        .filter(user__is_active=True, user__is_superuser=False)
        .exclude(user__email="")
        .order_by("id")
    )
    recipients: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        email = (getattr(profile.user, "email", "") or "").strip().casefold()
        if email and email not in seen:
            seen.add(email)
            recipients.append(email)
    return recipients


def _recipient_account_is_active(notification: SubscriptionEmailNotification) -> bool:
    return notification.subscription.school.users.filter(
        user__is_active=True,
        user__is_superuser=False,
        user__email__iexact=notification.recipient,
    ).exists()


def queue_invoice_email(invoice: SubscriptionInvoice) -> int:
    queued = 0
    for recipient in _school_recipient_emails(invoice.subscription):
        _, created = SubscriptionEmailNotification.objects.get_or_create(
            dedupe_key=f"invoice:{invoice.pk}:{recipient}"[:255],
            defaults={
                "event_type": SubscriptionEmailNotification.EventType.INVOICE,
                "subscription": invoice.subscription,
                "invoice": invoice,
                "recipient": recipient,
            },
        )
        queued += int(created)
    return queued


def queue_email_verification(subscription: SchoolSubscription, user) -> bool:
    """Queue an ownership-proof email for a newly registered account.

    Uses the same durable outbox as invoices so a slow or unavailable SMTP
    server can never block or fail the signup request itself.
    """
    recipient = (getattr(user, "email", "") or "").strip().casefold()
    if not recipient:
        return False

    _, created = SubscriptionEmailNotification.objects.get_or_create(
        dedupe_key=f"verify_email:{user.pk}:{recipient}"[:255],
        defaults={
            "event_type": SubscriptionEmailNotification.EventType.VERIFY_EMAIL,
            "subscription": subscription,
            "recipient": recipient,
        },
    )
    return created


def requeue_email_verification(subscription: SchoolSubscription, user) -> bool:
    """Let a customer ask for the verification email again."""
    recipient = (getattr(user, "email", "") or "").strip().casefold()
    if not recipient:
        return False

    updated = SubscriptionEmailNotification.objects.filter(
        dedupe_key=f"verify_email:{user.pk}:{recipient}"[:255],
    ).update(
        status=SubscriptionEmailNotification.Status.PENDING,
        available_at=timezone.now(),
        locked_at=None,
        attempts=0,
        last_error="",
    )
    if updated:
        return True
    return queue_email_verification(subscription, user)


def enqueue_expiry_email_reminders(*, on_date: date | None = None) -> int:
    if not email_notifications_enabled():
        return 0

    today = on_date or timezone.localdate()
    reminder_days = tuple(getattr(settings, "EMAIL_SUBSCRIPTION_EXPIRY_DAYS", ()) or ())
    if not reminder_days:
        return 0

    target_dates = [today + timedelta(days=int(days)) for days in reminder_days]
    subscriptions = (
        SchoolSubscription.objects.filter(status="active", ends_at__in=target_dates)
        .select_related("school", "plan")
        .order_by("ends_at", "id")
    )

    queued = 0
    for subscription in subscriptions.iterator():
        days_left = (subscription.ends_at - today).days
        if days_left not in reminder_days:
            continue
        for recipient in _school_recipient_emails(subscription):
            _, created = SubscriptionEmailNotification.objects.get_or_create(
                dedupe_key=(
                    f"expiry:{subscription.pk}:{subscription.ends_at.isoformat()}:"
                    f"{days_left}:{recipient}"
                )[:255],
                defaults={
                    "event_type": SubscriptionEmailNotification.EventType.EXPIRY,
                    "subscription": subscription,
                    "recipient": recipient,
                    "reminder_days": days_left,
                },
            )
            queued += int(created)
    return queued


def _absolute_url(path: str) -> str:
    base = str(getattr(settings, "SITE_BASE_URL", "") or "").strip().rstrip("/")
    return f"{base}{path}"


def _duration_label(days) -> str:
    """Say «6 أشهر» where the plan says 182, and never round a lie.

    A plan length is stored in days because that is what the billing maths
    needs, but nobody buys "182 days". The month wording is only used when it
    lands within a few days of the real length; anything else keeps the exact
    day count rather than being rounded into a figure the customer could hold
    us to.
    """
    try:
        days = int(days or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return "مفتوحة المدة"
    if days >= 365 and days % 365 == 0:
        years = days // 365
        return "سنة كاملة" if years == 1 else f"{years} سنوات"
    months = round(days / 30.44)
    if months >= 1 and abs(days - months * 30.44) <= 5:
        if months == 1:
            return "شهر واحد"
        if months == 2:
            return "شهران"
        if months <= 10:
            return f"{months} أشهر"
        return f"{months} شهرًا"
    return f"{days} يومًا"


def _arabic_count(count: int, one: str, two: str, few: str, many: str) -> str:
    """«شاشة واحدة»، «شاشتان»، «3 شاشات»، «11 شاشة».

    Arabic agrees the noun with the number in four forms, and getting it wrong
    ("3 شاشة") reads as broken machine output on a document the customer keeps.
    """
    if count == 1:
        return one
    if count == 2:
        return two
    if 3 <= count % 100 <= 10:
        return f"{count} {few}"
    return f"{count} {many}"


def _plan_features(plan) -> list[str]:
    raw = str(getattr(plan, "card_features", "") or "")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _invoice_context(invoice: SubscriptionInvoice) -> dict:
    """Everything the invoice email states about what was bought."""
    from .invoicing import _get_school_contact_info, _get_seller_info

    plan = invoice.plan
    subscription = invoice.subscription
    contact_name, _contact_mobile = _get_school_contact_info(
        invoice.school,
        preferred_user=getattr(invoice.operation, "created_by", None),
    )

    period_days = None
    if subscription.starts_at and subscription.ends_at:
        period_days = (subscription.ends_at - subscription.starts_at).days

    max_screens = getattr(plan, "max_screens", None)
    max_users = getattr(plan, "max_users", None)

    return {
        "seller": _get_seller_info(),
        "contact_name": contact_name,
        # Money is formatted here rather than in the template: Django localises
        # a Decimal into the active locale, which renders 2150.00 as «2150,00»
        # under Arabic — a decimal comma no Saudi invoice uses.
        "amount_display": f"{invoice.amount:,.2f}",
        "plan_price_display": f"{plan.price:,.2f}",
        "currency_label": invoice.get_currency_display(),
        "payment_method_label": invoice.get_payment_method_display(),
        "plan_features": _plan_features(plan),
        "plan_duration_label": _duration_label(getattr(plan, "duration_days", None)),
        "plan_duration_days": getattr(plan, "duration_days", None),
        "plan_screens_label": (
            _arabic_count(int(max_screens), "شاشة واحدة", "شاشتان", "شاشات", "شاشة")
            if max_screens
            else "عدد غير محدود من الشاشات"
        ),
        "plan_users_label": (
            _arabic_count(int(max_users), "مستخدم واحد", "مستخدمان", "مستخدمين", "مستخدمًا")
            if max_users
            else "عدد غير محدود من المستخدمين"
        ),
        "subscription_status_label": subscription.get_status_display(),
        "period_days": period_days,
    }


def _verification_recipient_user(notification: SubscriptionEmailNotification):
    """The account whose address this verification message proves."""
    profile = (
        notification.subscription.school.users.select_related("user")
        .filter(user__is_active=True, user__email__iexact=notification.recipient)
        .order_by("id")
        .first()
    )
    return getattr(profile, "user", None)


def _build_message(notification: SubscriptionEmailNotification) -> EmailMultiAlternatives:
    subscription = notification.subscription
    context = {
        "school": subscription.school,
        "subscription": subscription,
        "plan": subscription.plan,
        "recipient": notification.recipient,
        "subscription_url": _absolute_url(reverse("dashboard:my_subscription")),
    }

    if notification.event_type == SubscriptionEmailNotification.EventType.INVOICE:
        invoice = notification.invoice
        if invoice is None:
            raise ValueError("Invoice notification has no invoice")
        context.update(
            {
                "invoice": invoice,
                "invoice_url": _absolute_url(
                    reverse("dashboard:subscription_invoice_view", kwargs={"pk": invoice.pk})
                ),
                **_invoice_context(invoice),
            }
        )
        subject = f"فاتورة اشتراك {invoice.invoice_number} | لوحة العرض الذكية"
        text_body = render_to_string("emails/subscription_invoice.txt", context)
        html_body = render_to_string("emails/subscription_invoice.html", context)
    elif notification.event_type == SubscriptionEmailNotification.EventType.VERIFY_EMAIL:
        from core.email_verification import make_token

        user = _verification_recipient_user(notification)
        if user is None:
            raise ValueError("Verification notification has no matching account")

        context.update(
            {
                "user": user,
                "verify_url": _absolute_url(
                    reverse(
                        "website:verify_email",
                        kwargs={"token": make_token(user)},
                    )
                ),
                "valid_days": max(1, max_age_seconds() // 86400),
            }
        )
        subject = "تأكيد بريدك الإلكتروني | لوحة العرض الذكية"
        text_body = render_to_string("emails/verify_email.txt", context)
        html_body = render_to_string("emails/verify_email.html", context)
    else:
        days_left = int(notification.reminder_days or 0)
        context["days_left"] = days_left
        if days_left == 0:
            timing = "ينتهي اليوم"
        elif days_left == 1:
            timing = "متبقي يوم واحد"
        else:
            timing = f"متبقي {days_left} أيام"
        context["timing"] = timing
        subject = f"تنبيه: اشتراك {subscription.school.name} {timing}"
        text_body = render_to_string("emails/subscription_expiry.txt", context)
        html_body = render_to_string("emails/subscription_expiry.html", context)

    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[notification.recipient],
    )
    message.attach_alternative(html_body, "text/html")
    if notification.event_type == SubscriptionEmailNotification.EventType.INVOICE:
        invoice = notification.invoice
        if invoice and invoice.html_snapshot:
            message.attach(
                f"invoice-{invoice.invoice_number}.html",
                invoice.html_snapshot,
                "text/html",
            )
    return message


def _claim_next_notification() -> int | None:
    with transaction.atomic():
        notification = (
            SubscriptionEmailNotification.objects.select_for_update(skip_locked=True)
            .filter(
                status=SubscriptionEmailNotification.Status.PENDING,
                available_at__lte=timezone.now(),
            )
            .order_by("created_at", "id")
            .first()
        )
        if notification is None:
            return None
        notification.status = SubscriptionEmailNotification.Status.PROCESSING
        notification.locked_at = timezone.now()
        notification.attempts += 1
        notification.save(update_fields=("status", "locked_at", "attempts", "updated_at"))
        return int(notification.pk)


def _retry_delay_seconds(attempts: int) -> int:
    return min(3600, 15 * (2 ** max(0, min(8, attempts - 1))))


def _deliver_claimed(notification_id: int) -> str:
    notification = SubscriptionEmailNotification.objects.select_related(
        "subscription__school", "subscription__plan", "invoice"
    ).get(pk=notification_id)
    if not _recipient_account_is_active(notification):
        SubscriptionEmailNotification.objects.filter(
            pk=notification.pk,
            status=SubscriptionEmailNotification.Status.PROCESSING,
        ).update(
            status=SubscriptionEmailNotification.Status.SKIPPED,
            locked_at=None,
            last_error="Recipient account is inactive or no longer linked to the school.",
        )
        return "skipped"
    try:
        sent_count = _build_message(notification).send(fail_silently=False)
        if sent_count != 1:
            raise RuntimeError(f"Email backend returned sent_count={sent_count}")
    except Exception as exc:
        max_attempts = int(settings.EMAIL_NOTIFICATION_MAX_ATTEMPTS)
        final_failure = notification.attempts >= max_attempts
        SubscriptionEmailNotification.objects.filter(
            pk=notification.pk,
            status=SubscriptionEmailNotification.Status.PROCESSING,
        ).update(
            status=(
                SubscriptionEmailNotification.Status.FAILED
                if final_failure
                else SubscriptionEmailNotification.Status.PENDING
            ),
            locked_at=None,
            available_at=timezone.now()
            + timedelta(seconds=_retry_delay_seconds(notification.attempts)),
            last_error=str(exc)[:2000],
        )
        logger.warning(
            "subscription_email_delivery_failed notification_id=%s attempts=%s final=%s",
            notification.pk,
            notification.attempts,
            int(final_failure),
        )
        return "failed" if final_failure else "retried"

    SubscriptionEmailNotification.objects.filter(
        pk=notification.pk,
        status=SubscriptionEmailNotification.Status.PROCESSING,
    ).update(
        status=SubscriptionEmailNotification.Status.SENT,
        locked_at=None,
        sent_at=timezone.now(),
        last_error="",
    )
    return "sent"


def reset_stale_processing(*, older_than_seconds: int = 300) -> int:
    cutoff = timezone.now() - timedelta(seconds=max(60, older_than_seconds))
    return SubscriptionEmailNotification.objects.filter(
        status=SubscriptionEmailNotification.Status.PROCESSING,
        locked_at__lt=cutoff,
    ).update(
        status=SubscriptionEmailNotification.Status.PENDING,
        locked_at=None,
        available_at=timezone.now(),
        last_error="Recovered after a stale worker lock.",
    )


def process_pending_email_notifications(*, limit: int | None = None) -> DeliveryResult:
    if not email_notifications_enabled():
        return DeliveryResult()
    batch_size = max(1, min(100, int(limit or settings.EMAIL_NOTIFICATION_BATCH_SIZE)))
    sent = skipped = retried = failed = 0
    for _ in range(batch_size):
        notification_id = _claim_next_notification()
        if notification_id is None:
            break
        outcome = _deliver_claimed(notification_id)
        sent += int(outcome == "sent")
        skipped += int(outcome == "skipped")
        retried += int(outcome == "retried")
        failed += int(outcome == "failed")
    return DeliveryResult(
        sent=sent,
        skipped=skipped,
        retried=retried,
        failed=failed,
    )


def touch_worker_heartbeat(*, state: str = "running") -> None:
    cache.set(
        WORKER_HEARTBEAT_KEY,
        {"timestamp": timezone.now().timestamp(), "state": state},
        timeout=WORKER_HEARTBEAT_TTL_SECONDS,
    )


def worker_is_alive() -> bool:
    heartbeat = cache.get(WORKER_HEARTBEAT_KEY)
    if not isinstance(heartbeat, dict):
        return False
    try:
        age = timezone.now().timestamp() - float(heartbeat["timestamp"])
    except (KeyError, TypeError, ValueError):
        return False
    return age <= WORKER_HEARTBEAT_TTL_SECONDS

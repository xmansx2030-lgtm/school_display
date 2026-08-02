from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from subscriptions.email_notifications import (
    email_notifications_enabled,
    enqueue_expiry_email_reminders,
    process_pending_email_notifications,
    reset_stale_processing,
    touch_worker_heartbeat,
    worker_is_alive,
)
from subscriptions.invoicing import reconcile_missing_invoices

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Deliver queued invoice emails and subscription expiry reminders."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--healthcheck", action="store_true")
        parser.add_argument("--interval", type=int, default=None)

    def handle(self, *args, **options):
        if options["healthcheck"]:
            if not email_notifications_enabled():
                self.stdout.write(self.style.SUCCESS("Transactional email is disabled."))
                return
            if not worker_is_alive():
                raise CommandError("Email notification worker is not alive.")
            self.stdout.write(self.style.SUCCESS("Email notification worker is alive."))
            return

        once = bool(options["once"])
        interval = max(
            5,
            min(
                300,
                int(options["interval"] or settings.EMAIL_NOTIFICATION_POLL_INTERVAL_SECONDS),
            ),
        )
        last_expiry_scan_date = None

        while True:
            state = "running" if email_notifications_enabled() else "disabled"
            touch_worker_heartbeat(state=state)
            try:
                reconciled = reconcile_missing_invoices()
                if reconciled:
                    self.stdout.write(f"invoice_reconciliation created={reconciled}")
                if email_notifications_enabled():
                    today = timezone.localdate()
                    if last_expiry_scan_date != today:
                        queued = enqueue_expiry_email_reminders(on_date=today)
                        last_expiry_scan_date = today
                        self.stdout.write(
                            f"email_expiry_scan date={today.isoformat()} queued={queued}"
                        )
                    reset_stale_processing()
                    result = process_pending_email_notifications()
                    if result.sent or result.retried or result.failed:
                        self.stdout.write(
                            "email_delivery "
                            f"sent={result.sent} retried={result.retried} failed={result.failed}"
                        )
            except Exception:
                logger.exception("email_notification_worker_cycle_failed")

            touch_worker_heartbeat(state=state)
            if once:
                return
            time.sleep(interval)

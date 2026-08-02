from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from subscriptions.tamara_processing import (
    reconcile_pending_checkouts,
    touch_worker_heartbeat,
    worker_is_alive,
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconcile pending Tamara checkouts using Tamara's order-details API."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--healthcheck", action="store_true")
        parser.add_argument("--interval", type=int, default=None)

    def handle(self, *args, **options):
        if options["healthcheck"]:
            if not getattr(settings, "TAMARA_ENABLED", False):
                self.stdout.write(self.style.SUCCESS("Tamara is disabled."))
                return
            if not worker_is_alive():
                raise CommandError("Tamara reconciliation worker is not alive.")
            self.stdout.write(self.style.SUCCESS("Tamara reconciliation worker is alive."))
            return

        once = bool(options["once"])
        interval = max(
            5,
            min(
                300,
                int(options["interval"] or settings.TAMARA_RECONCILIATION_INTERVAL_SECONDS),
            ),
        )

        while True:
            touch_worker_heartbeat()
            try:
                result = reconcile_pending_checkouts()
                if result.checked or result.failed:
                    self.stdout.write(
                        "tamara_reconciliation "
                        f"checked={result.checked} activated={result.activated} "
                        f"captured={result.captured} failed={result.failed}"
                    )
            except Exception:
                logger.exception("tamara_reconciliation_worker_cycle_failed")
            touch_worker_heartbeat()
            if once:
                return
            time.sleep(interval)

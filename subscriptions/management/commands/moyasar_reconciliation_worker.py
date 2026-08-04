from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from subscriptions.moyasar_processing import (
    reconcile_pending_checkouts,
    touch_worker_heartbeat,
    worker_is_alive,
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Reconcile pending Moyasar checkouts against Moyasar's payment records."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--healthcheck", action="store_true")
        parser.add_argument("--interval", type=int, default=None)

    def handle(self, *args, **options):
        if options["healthcheck"]:
            if not getattr(settings, "MOYASAR_ENABLED", False):
                self.stdout.write(self.style.SUCCESS("Moyasar is disabled."))
                return
            if not worker_is_alive():
                raise CommandError("Moyasar reconciliation worker is not alive.")
            self.stdout.write(self.style.SUCCESS("Moyasar reconciliation worker is alive."))
            return

        once = bool(options["once"])
        interval = max(
            5,
            min(
                300,
                int(options["interval"] or getattr(settings, "MOYASAR_RECONCILIATION_INTERVAL_SECONDS", 60)),
            ),
        )

        while True:
            touch_worker_heartbeat()
            try:
                result = reconcile_pending_checkouts()
                if result.touched:
                    self.stdout.write(
                        "moyasar_reconciliation "
                        f"checked={result.checked} matched={result.matched} "
                        f"activated={result.activated} expired={result.expired} "
                        f"failed={result.failed}"
                    )
            except Exception:
                logger.exception("moyasar_reconciliation_worker_cycle_failed")
            touch_worker_heartbeat()
            if once:
                return
            time.sleep(interval)

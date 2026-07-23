"""
display_wake_scheduler — long-running worker that wakes sleeping displays
~30 minutes before each school's active window starts.

Idempotent across multiple workers via Redis cache dedupe in
`schedule.wake_broadcaster.maybe_fire_pre_active_wake`.
"""
from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from schedule.models import SchoolSettings
from schedule.wake_broadcaster import maybe_fire_pre_active_wake, touch_wake_scheduler_heartbeat

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Background worker: broadcast wake/reload to displays before active window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval", type=int, default=60,
            help="Seconds between scans (default 60).",
        )
        parser.add_argument(
            "--lead-minutes", type=int, default=30,
            help="Wake this many minutes before active_start (default 30).",
        )
        parser.add_argument(
            "--window-seconds", type=int, default=90,
            help="Acceptable +/- window around the wake target (default 90s).",
        )
        parser.add_argument(
            "--once", action="store_true",
            help="Run a single scan and exit (for tests).",
        )

    def handle(self, *args, **options):
        interval = max(15, int(options.get("interval") or 60))
        lead_minutes = max(1, int(options.get("lead_minutes") or 30))
        window_seconds = max(30, int(options.get("window_seconds") or 90))
        once = bool(options.get("once"))

        self.stdout.write(
            f"wake_scheduler_started interval={interval}s lead={lead_minutes}m window=±{window_seconds}s"
        )
        worker_id = f"wake-scheduler:{int(time.time())}"

        while True:
            scanned = 0
            fired = 0
            try:
                touch_wake_scheduler_heartbeat(worker_id=worker_id)
                qs = (
                    SchoolSettings.objects.filter(school__screens__is_active=True)
                    .exclude(school__screens__bound_device_id__isnull=True)
                    .exclude(school__screens__bound_device_id="")
                    .only(
                        "id",
                        "school_id",
                        "timezone_name",
                        "schedule_revision",
                        "test_mode_weekday_override",
                    )
                    .distinct()
                )
                for s in qs.iterator():
                    scanned += 1
                    if scanned % 100 == 0:
                        touch_wake_scheduler_heartbeat(worker_id=worker_id)
                    try:
                        slot = maybe_fire_pre_active_wake(
                            s,
                            lead_minutes=lead_minutes,
                            window_seconds=window_seconds,
                        )
                        if slot:
                            fired += 1
                            self.stdout.write(
                                f"wake_fired school_id={getattr(s, 'school_id', '?')} slot={slot}"
                            )
                    except Exception as exc:
                        logger.exception(
                            "wake_scheduler_school_error school_id=%s: %s",
                            getattr(s, "school_id", "?"), exc,
                        )
            except Exception as exc:
                logger.exception("wake_scheduler_scan_error: %s", exc)

            touch_wake_scheduler_heartbeat(worker_id=worker_id)

            if scanned == 0 or fired:
                self.stdout.write(
                    f"wake_scheduler_scan scanned={scanned} fired={fired}"
                )

            if once:
                return
            time.sleep(interval)

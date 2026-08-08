from __future__ import annotations

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.screen_monitoring import (
    prune_operational_data,
    scan_screens,
    send_weekly_uptime_reports,
)


HEARTBEAT_KEY = "screen-monitor:heartbeat"
PRUNE_LOCK_KEY = "screen-monitor:prune:{day}"
PRUNE_HOUR = 3


def _maybe_prune(now) -> dict | None:
    """Run the retention pass once a day, off-peak.

    The lock lives in the shared cache, so a second monitor process — or this
    one restarting — cannot repeat the pass. Losing the lock to a cache flush
    would at worst run a second pass that finds nothing left to delete.
    """
    if now.hour < PRUNE_HOUR:
        return None
    key = PRUNE_LOCK_KEY.format(day=now.date().isoformat())
    if not cache.add(key, "1", timeout=36 * 60 * 60):
        return None
    return prune_operational_data(now=now)


class Command(BaseCommand):
    help = "يراقب اتصال شاشات العرض ويرسل تنبيهات التعطل وتقارير التشغيل الأسبوعية."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--weekly", action="store_true")
        parser.add_argument("--prune", action="store_true", help="شغّل تقليم السجلات القديمة الآن ثم اخرج.")
        parser.add_argument("--interval", type=int, default=60)
        parser.add_argument("--healthcheck", action="store_true")

    def handle(self, *args, **options):
        if options["healthcheck"]:
            heartbeat = cache.get(HEARTBEAT_KEY)
            if not heartbeat:
                raise SystemExit("screen monitor heartbeat is missing")
            self.stdout.write("ok")
            return
        if options["weekly"]:
            self.stdout.write(str(send_weekly_uptime_reports()))
            return
        if options["prune"]:
            self.stdout.write(str(prune_operational_data()))
            return
        interval = max(30, int(options["interval"]))
        while True:
            cache.set(HEARTBEAT_KEY, timezone.now().isoformat(), timeout=interval * 3)
            result = scan_screens()
            local_now = timezone.localtime()
            if local_now.weekday() == 0 and local_now.hour >= 7:
                send_weekly_uptime_reports(now=local_now)
            pruned = _maybe_prune(local_now)
            if pruned:
                self.stdout.write(f"pruned {pruned}")
            self.stdout.write(str(result))
            if options["once"]:
                return
            time.sleep(interval)

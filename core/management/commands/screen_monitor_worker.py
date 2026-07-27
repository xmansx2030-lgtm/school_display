from __future__ import annotations

import time

from django.core.cache import cache
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.screen_monitoring import scan_screens, send_weekly_uptime_reports


HEARTBEAT_KEY = "screen-monitor:heartbeat"


class Command(BaseCommand):
    help = "يراقب اتصال شاشات العرض ويرسل تنبيهات التعطل وتقارير التشغيل الأسبوعية."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--weekly", action="store_true")
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
        interval = max(30, int(options["interval"]))
        while True:
            cache.set(HEARTBEAT_KEY, timezone.now().isoformat(), timeout=interval * 3)
            result = scan_screens()
            local_now = timezone.localtime()
            if local_now.weekday() == 0 and local_now.hour >= 7:
                send_weekly_uptime_reports(now=local_now)
            self.stdout.write(str(result))
            if options["once"]:
                return
            time.sleep(interval)

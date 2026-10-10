"""The saved weekly timetable is usable only inside a known school calendar."""
import shutil
import subprocess
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from schedule.models import DaySchedule, Period, SchoolClosure, SchoolSettings
from schedule.time_engine import build_day_snapshot


@skipUnless(shutil.which("node"), "Node.js is required for offline display runtime tests")
class OfflineCalendarRuntimeTests(SimpleTestCase):
    def test_saved_calendar_runtime(self):
        test_file = Path(__file__).resolve().parent.parent / "tests/js/display-offline-calendar.test.cjs"
        result = subprocess.run(
            [shutil.which("node"), "--test", str(test_file)],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class OfflineCalendarSnapshotTests(TestCase):
    def setUp(self):
        cache.clear()
        self.settings = SchoolSettings.objects.create(name="Offline calendar", timezone_name="Asia/Riyadh")
        for weekday in (1, 2, 3):
            day = DaySchedule.objects.create(settings=self.settings, weekday=weekday, is_active=True)
            Period.objects.create(day=day, index=1, starts_at=time(8), ends_at=time(9))

    def tearDown(self):
        cache.clear()

    def snapshot(self, now="2026-10-12T08:30:00+03:00", settings=None):
        return build_day_snapshot(settings or self.settings, now=datetime.fromisoformat(now))

    def closure(self, **kwargs):
        values = dict(settings=self.settings, title="School closure", start_date=date(2026, 10, 12), end_date=date(2026, 10, 13))
        values.update(kwargs)
        return SchoolClosure.objects.create(**values)

    def test_two_day_closure_is_saved_alongside_reusable_weekday_plan(self):
        self.closure()
        snap = self.snapshot()
        calendar = snap["meta"]["offline_calendar"]
        self.assertEqual(snap["state"]["reason"], "holiday")
        self.assertTrue(snap["meta"]["week_plan"]["2"])
        self.assertEqual(calendar["closed_dates"], {"2026-10-12": "School closure", "2026-10-13": "School closure"})
        self.assertEqual(snap["meta"]["next_school_day"]["date"], "2026-10-14")
        self.assertEqual(calendar["valid_from"], "2026-10-12")
        self.assertEqual(calendar["valid_through"], "2026-11-01")
        self.assertEqual(calendar["utc_offset_minutes"], 180)

    def test_range_is_bounded_and_inactive_closures_do_not_block_it(self):
        self.closure(start_date=date(2026, 9, 1), end_date=date(2026, 12, 31), title="")
        closed = self.snapshot()["meta"]["offline_calendar"]["closed_dates"]
        self.assertEqual(len(closed), 21)
        self.assertEqual(closed["2026-10-13"], "")
        self.assertNotIn("2026-11-02", closed)
        self.assertNotIn("2026-10-11", closed)
        cache.clear()
        SchoolClosure.objects.all().update(is_active=False)
        self.assertEqual(self.snapshot()["meta"]["offline_calendar"]["closed_dates"], {})

    def test_other_settings_do_not_share_closure_cache_even_without_school_fk(self):
        self.closure()
        other = SchoolSettings.objects.create(name="Other", timezone_name="Asia/Riyadh")
        day = DaySchedule.objects.create(settings=other, weekday=1, is_active=True)
        Period.objects.create(day=day, index=1, starts_at=time(8), ends_at=time(9))
        self.assertTrue(self.snapshot()["meta"]["offline_calendar"]["closed_dates"])
        self.assertEqual(self.snapshot(settings=other)["meta"]["offline_calendar"]["closed_dates"], {})

    def test_calendar_dates_follow_school_timezone_not_utc_or_host(self):
        self.settings.timezone_name = "Pacific/Auckland"
        snap = self.snapshot("2026-10-11T12:30:00+00:00")
        calendar = snap["meta"]["offline_calendar"]
        self.assertEqual(snap["meta"]["date"], "2026-10-12")
        self.assertEqual(calendar["valid_from"], "2026-10-12")
        self.assertEqual(calendar["timezone_name"], "Pacific/Auckland")
        self.assertEqual(calendar["utc_offset_minutes"], 780)

    def test_known_window_stops_before_dst_can_change_cached_wall_clock(self):
        self.settings.timezone_name = "America/New_York"
        calendar = self.snapshot("2026-10-30T08:30:00-04:00")["meta"]["offline_calendar"]
        self.assertEqual(calendar["valid_through"], "2026-10-31")
        self.assertEqual(calendar["utc_offset_minutes"], -240)
        transition = self.snapshot("2026-11-01T08:30:00-05:00")
        self.assertNotIn("offline_calendar", transition["meta"])
        self.assertEqual(self.snapshot("2026-11-02T08:30:00-05:00")["meta"]["offline_calendar"]["utc_offset_minutes"], -300)

    def test_invalid_timezone_uses_same_riyadh_fallback_as_online_snapshot(self):
        self.settings.timezone_name = "Unknown/Timezone"
        calendar = self.snapshot()["meta"]["offline_calendar"]
        self.assertEqual(calendar["timezone_name"], "Asia/Riyadh")
        self.assertEqual(calendar["utc_offset_minutes"], 180)

    def test_single_cached_closure_read_serves_all_calendar_consumers(self):
        self.closure()
        period = SimpleNamespace(index=1, starts_at=time(8), ends_at=time(9), subject=None, teacher=None, school_class=None)
        day = SimpleNamespace(_prefetched_objects_cache={"periods": [period], "breaks": []})
        with patch("schedule.time_engine._build_active_days_index", return_value={1: [day], 2: [day], 3: [day]}):
            with self.assertNumQueries(1):
                snap = self.snapshot()
            with self.assertNumQueries(0):
                self.assertEqual(self.snapshot()["meta"]["offline_calendar"], snap["meta"]["offline_calendar"])
            # A new schedule revision must not reuse an older closure result.
            self.settings.schedule_revision += 1
            with self.assertNumQueries(1):
                self.snapshot()

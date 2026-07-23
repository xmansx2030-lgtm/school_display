from __future__ import annotations

import io
import json
import os
from datetime import datetime, time as dt_time, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.models import DisplayScreen, School
from schedule import api_views as av
from schedule import cache_utils as cu
from schedule import signals as sig
from schedule import snapshot_materializer as sm
from schedule import snapshot_observability as so
from schedule.management.commands.display_snapshot_worker import Command as SnapshotWorkerCommand
from notices.models import Announcement, Excellence
from schedule.models import (
    Break,
    ClassLesson,
    DaySchedule,
    DutyAssignment,
    Period,
    SchoolClass,
    SchoolSettings,
    Subject,
    Teacher,
)
from standby.models import StandbyAssignment
from schedule.time_engine import build_day_snapshot


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    def get(self, key):
        return self.store.get(str(key))

    def set(self, key, value, nx=False, ex=None):
        key = str(key)
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def delete(self, key):
        return int(self.store.pop(str(key), None) is not None)

    def expire(self, key, ttl):
        return bool(str(key) in self.store)

    def rpush(self, key, value):
        self.lists.setdefault(str(key), []).append(value)
        return len(self.lists[str(key)])

    def blpop(self, key, timeout=0):
        items = self.lists.setdefault(str(key), [])
        if not items:
            return None
        return str(key), items.pop(0)

    def llen(self, key):
        return len(self.lists.get(str(key), []))

    def eval(self, script, numkeys, key, *args):
        raise NotImplementedError


class TimeEngineTestModeOverrideTests(TestCase):
    def setUp(self):
        self.settings = SchoolSettings.objects.create(
            name="مدرسة اختبار الوقت",
            timezone_name="Asia/Riyadh",
            test_mode_weekday_override=1,
        )
        monday = DaySchedule.objects.create(settings=self.settings, weekday=1, is_active=True)
        tuesday = DaySchedule.objects.create(settings=self.settings, weekday=2, is_active=True)

        Period.objects.create(day=monday, index=1, starts_at=dt_time(8, 30), ends_at=dt_time(9, 15))
        Period.objects.create(day=tuesday, index=1, starts_at=dt_time(2, 30), ends_at=dt_time(3, 20))

    def test_real_active_weekday_takes_precedence_over_test_override(self):
        snap = build_day_snapshot(self.settings, now=datetime.fromisoformat("2026-06-02T02:31:00+03:00"))

        self.assertEqual(snap["meta"]["weekday"], 2)
        self.assertEqual(snap["state"]["type"], "period")
        self.assertEqual(snap["state"]["period_index"], 1)
        self.assertEqual(snap["state"]["from"], "02:30")

    def test_test_override_still_applies_when_actual_day_has_no_schedule(self):
        snap = build_day_snapshot(self.settings, now=datetime.fromisoformat("2026-06-06T08:35:00+03:00"))

        self.assertEqual(snap["meta"]["weekday"], 1)
        self.assertEqual(snap["state"]["type"], "period")
        self.assertEqual(snap["state"]["from"], "08:30")


@override_settings(
    DISPLAY_SNAPSHOT_ASYNC_BUILD=True,
    DISPLAY_SNAPSHOT_REQUIRE_WORKER_ALIVE=False,
    DISPLAY_SNAPSHOT_DEBOUNCE_SEC=0,
    DISPLAY_SNAPSHOT_PENDING_TTL_SEC=30,
    DISPLAY_SNAPSHOT_LATEST_REV_TTL_SEC=120,
)
class SnapshotQueueCoalescingTests(SimpleTestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.patches = [
            patch.object(sm, "_get_queue_redis_connection", return_value=self.redis),
            patch.object(sm, "snapshot_queue_available", return_value=True),
            patch.object(sm, "_metrics_incr", lambda *args, **kwargs: None),
            patch.object(sm, "_metrics_add", lambda *args, **kwargs: None),
            patch.object(sm, "_metrics_set_max", lambda *args, **kwargs: None),
            patch.object(sm, "_obs_snapshot_queue", lambda *args, **kwargs: None),
            patch.object(sm, "_obs_snapshot_build", lambda *args, **kwargs: None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_rapid_triggers_keep_one_pending_job_and_latest_revision(self):
        first = sm.enqueue_snapshot_build(school_id=1, rev=1080, day_key="2026-04-20")
        second = sm.enqueue_snapshot_build(school_id=1, rev=1081, day_key="2026-04-20")
        third = sm.enqueue_snapshot_build(school_id=1, rev=1082, day_key="2026-04-20")

        self.assertTrue(first["queued"])
        self.assertTrue(second["duplicate"])
        self.assertTrue(third["duplicate"])
        self.assertEqual(self.redis.llen(sm._queue_name()), 1)
        self.assertEqual(self.redis.get(sm._latest_rev_key(1, "2026-04-20")), "1082")

    def test_dequeue_coalesces_old_job_to_latest_revision(self):
        sm.enqueue_snapshot_build(school_id=1, rev=1080, day_key="2026-04-20")
        sm.enqueue_snapshot_build(school_id=1, rev=1083, day_key="2026-04-20")

        job = sm.dequeue_snapshot_build(block_timeout=0)

        self.assertIsNotNone(job)
        self.assertEqual(job["rev"], 1083)
        self.assertEqual(job["_coalesced_from_rev"], 1080)

    @override_settings(DISPLAY_SNAPSHOT_DEBOUNCE_SEC=3)
    def test_enqueue_debounce_skips_second_job_but_updates_latest_revision(self):
        first = sm.enqueue_snapshot_build(school_id=1, rev=1080, day_key="2026-04-20")
        second = sm.enqueue_snapshot_build(school_id=1, rev=1081, day_key="2026-04-20")

        self.assertTrue(first["queued"])
        self.assertEqual(first["reason"], "queued")
        self.assertFalse(first["debounced"])
        self.assertFalse(first["deduped"])
        self.assertFalse(first["coalesced"])
        self.assertFalse(second["queued"])
        self.assertEqual(second["reason"], "debounced")
        self.assertTrue(second["debounced"])
        self.assertFalse(second["deduped"])
        self.assertTrue(second["coalesced"])
        self.assertEqual(self.redis.llen(sm._queue_name()), 1)
        self.assertEqual(self.redis.get(sm._latest_rev_key(1, "2026-04-20")), "1081")

    def test_already_materialized_latest_job_is_marked_skippable(self):
        sm.enqueue_snapshot_build(school_id=1, rev=1080, day_key="2026-04-20")
        self.redis.set(sm._materialized_rev_key(1, "2026-04-20"), "1081")

        job = sm.dequeue_snapshot_build(block_timeout=0)

        self.assertIsNotNone(job)
        self.assertTrue(job["_skip_complete"])
        self.assertEqual(job["_skip_reason"], "already_materialized_latest")

    def test_different_schools_and_days_do_not_collide(self):
        sm.enqueue_snapshot_build(school_id=1, rev=10, day_key="2026-04-20")
        sm.enqueue_snapshot_build(school_id=2, rev=10, day_key="2026-04-20")
        sm.enqueue_snapshot_build(school_id=1, rev=10, day_key="2026-04-21")

        self.assertEqual(self.redis.llen(sm._queue_name()), 3)
        self.assertEqual(self.redis.get(sm._latest_rev_key(1, "2026-04-20")), "10")
        self.assertEqual(self.redis.get(sm._latest_rev_key(2, "2026-04-20")), "10")
        self.assertEqual(self.redis.get(sm._latest_rev_key(1, "2026-04-21")), "10")

    def test_worker_unavailable_fallback_still_skips_enqueue(self):
        with override_settings(DISPLAY_SNAPSHOT_REQUIRE_WORKER_ALIVE=True):
            with patch.object(sm, "snapshot_worker_status", return_value={"alive": False}):
                result = sm.enqueue_snapshot_build(school_id=1, rev=10, day_key="2026-04-20")

        self.assertFalse(result["queued"])
        self.assertEqual(result["reason"], "worker_unavailable")
        self.assertEqual(self.redis.llen(sm._queue_name()), 0)

    def test_pending_job_completion_does_not_delete_newer_pending_job(self):
        first = sm.enqueue_snapshot_build(school_id=1, rev=10, day_key="2026-04-20")["job"]
        pending_key = sm._pending_job_key(1, "2026-04-20")
        self.redis.set(pending_key, "newer-job")

        sm.complete_snapshot_job(first)

        self.assertEqual(self.redis.get(pending_key), "newer-job")

    def test_enqueue_skips_when_latest_revision_is_already_cached(self):
        snap = {
            "meta": {"schedule_revision": 1081, "is_active_window": True},
            "settings": {"refresh_interval_sec": 30},
            "state": {"type": "period"},
            "day_path": [],
            "period_classes": [],
            "period_classes_map": {},
            "standby": [],
            "excellence": [],
            "announcements": [],
        }
        steady_key = av._steady_cache_key_for_school_rev(1, 1081, day_key="2026-04-20")
        fake_cache = SimpleNamespace(
            get=lambda key: av._snapshot_cache_entry(snap) if key == steady_key else None
        )

        with patch.object(sm, "cache", fake_cache):
            result = sm.enqueue_snapshot_build(school_id=1, rev=1081, day_key="2026-04-20")

        self.assertFalse(result["queued"])
        self.assertEqual(result["reason"], "already_cached")
        self.assertEqual(self.redis.llen(sm._queue_name()), 0)

    def test_enqueue_payload_uses_latest_revision_when_revision_advances(self):
        with patch.object(sm, "_redis_set_latest_revision", return_value=(1082, True)):
            result = sm.enqueue_snapshot_build(school_id=1, rev=1080, day_key="2026-04-20")

        self.assertTrue(result["queued"])
        self.assertEqual(result["job"]["rev"], 1082)


class SnapshotCacheValidationTests(SimpleTestCase):
    def test_validated_cache_entry_rejects_older_revision(self):
        snap = {
            "meta": {"schedule_revision": 5, "is_active_window": True},
            "settings": {"refresh_interval_sec": 30},
            "state": {"type": "period"},
            "day_path": [],
            "period_classes": [],
            "period_classes_map": {},
            "standby": [],
            "excellence": [],
            "announcements": [],
        }

        entry, reason = av._validated_snapshot_cache_entry_from_value(
            av._snapshot_cache_entry(snap),
            min_rev=6,
        )

        self.assertIsNone(entry)
        self.assertEqual(reason, "older_revision")

    def test_validated_cache_entry_rejects_before_hours_after_wake_boundary(self):
        snap = {
            "meta": {
                "schedule_revision": 6,
                "is_active_window": False,
                "next_wake_at": (timezone.now() - timedelta(minutes=1)).isoformat(),
            },
            "settings": {"refresh_interval_sec": 60},
            "state": {"type": "off", "reason": "before_hours"},
            "day_path": [],
            "period_classes": [],
            "period_classes_map": {},
            "standby": [],
            "excellence": [],
            "announcements": [],
        }

        entry, reason = av._validated_snapshot_cache_entry_from_value(
            av._snapshot_cache_entry(snap),
            min_rev=6,
        )

        self.assertIsNone(entry)
        self.assertEqual(reason, "past_wake_boundary")


class SnapshotInvalidationTests(TestCase):
    def test_invalidation_deletes_current_snapshot_namespace_keys(self):
        school = School.objects.create(name="مدرسة الكاش", slug="cache-school")
        screen = DisplayScreen.objects.create(school=school, name="الشاشة")
        token_hash = cu.sha256(screen.token)
        today = timezone.localdate().isoformat()
        settings = SchoolSettings.objects.create(name="مدرسة الكاش", school=school, schedule_revision=12)

        keys = []
        for ns in cu.SNAPSHOT_CACHE_NAMESPACES:
            keys.extend([
                f"snapshot:{ns}:school:{school.id}:day:{today}",
                f"snapshot:last:{ns}:{school.id}:{today}",
                f"display:snapshot:{ns}:{token_hash}",
                f"display:snapshot:{ns}:{school.id}:{token_hash}",
                f"display:snapshot:{ns}:{school.id}:rev:{settings.schedule_revision}:{token_hash}",
            ])
        for key in keys:
            cu.cache.set(key, "stale", timeout=300)

        cu.invalidate_display_snapshot_cache_for_school_id(school.id)

        for key in keys:
            with self.subTest(key=key):
                self.assertIsNone(cu.cache.get(key))


class DisplayWebSocketInvalidationTests(SimpleTestCase):
    @override_settings(DISPLAY_WS_ENABLED=True)
    def test_websocket_invalidation_broadcasts_every_nearby_update(self):
        class FakeChannelLayer:
            def __init__(self):
                self.sent = []

            async def group_send(self, group_name, event):
                self.sent.append((group_name, event))

        layer = FakeChannelLayer()

        with patch("channels.layers.get_channel_layer", return_value=layer):
            sig._broadcast_invalidate_ws(7, 101)
            sig._broadcast_invalidate_ws(7, 102)

        self.assertEqual(len(layer.sent), 2)
        self.assertEqual([event["revision"] for _, event in layer.sent], [101, 102])


class DisplaySignalInvalidationTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة التحديثات", slug="updates-school")
        self.settings = SchoolSettings.objects.create(
            name="مدرسة التحديثات",
            school=self.school,
            timezone_name="Asia/Riyadh",
        )
        self.screen = DisplayScreen.objects.create(school=self.school, name="الشاشة الرئيسية")
        self.day = DaySchedule.objects.create(settings=self.settings, weekday=2, is_active=True)
        self.school_class = SchoolClass.objects.create(settings=self.settings, name="الأول أ")
        self.subject = Subject.objects.create(school=self.school, name="رياضيات")
        self.teacher = Teacher.objects.create(school=self.school, name="أحمد")
        self.token_hash = cu.sha256(self.screen.token)
        cu.cache.clear()

    def _prime_snapshot_keys(self) -> list[str]:
        self.settings.refresh_from_db()
        today = timezone.localdate().isoformat()
        rev = int(self.settings.schedule_revision or 0)
        keys = []
        for ns in cu.SNAPSHOT_CACHE_NAMESPACES:
            keys.extend([
                f"snapshot:{ns}:school:{self.school.id}:day:{today}",
                f"snapshot:last:{ns}:{self.school.id}:{today}",
                f"display:snapshot:{ns}:{self.token_hash}",
                f"display:snapshot:{ns}:{self.school.id}:{self.token_hash}",
                f"display:snapshot:{ns}:{self.school.id}:rev:{rev}:{self.token_hash}",
                f"display:snapshot:{ns}:{self.school.id}:rev:{rev + 1}:{self.token_hash}",
            ])
        for key in keys:
            cu.cache.set(key, "stale", timeout=300)
        cu.cache.delete(f"display:force_refresh:{self.token_hash}")
        cu.cache.delete(f"display:rev_bump_window:{self.school.id}")
        return keys

    def _assert_dashboard_source_change_refreshes_display(self, mutate):
        keys = self._prime_snapshot_keys()

        mutate()

        for key in keys:
            with self.subTest(key=key):
                self.assertIsNone(cu.cache.get(key))
        self.assertEqual(cu.cache.get(f"display:force_refresh:{self.token_hash}"), "1")

    def test_standby_assignment_save_refreshes_display_without_manual_reload(self):
        self._assert_dashboard_source_change_refreshes_display(
            lambda: StandbyAssignment.objects.create(
                school=self.school,
                date=timezone.localdate(),
                period_index=3,
                class_name="الأول أ",
                teacher_name="معلم انتظار",
                notes="اختبار",
            )
        )

    def test_all_snapshot_source_saves_refresh_display_without_manual_reload(self):
        sources = [
            (
                "settings",
                lambda: (
                    setattr(self.settings, "display_before_badge", "مرحبا"),
                    self.settings.save(),
                ),
            ),
            (
                "period",
                lambda: Period.objects.create(
                    day=self.day,
                    index=1,
                    starts_at=dt_time(8, 0),
                    ends_at=dt_time(8, 45),
                ),
            ),
            (
                "break",
                lambda: Break.objects.create(
                    day=self.day,
                    label="فسحة",
                    starts_at=dt_time(9, 0),
                    duration_min=15,
                ),
            ),
            (
                "class_lesson",
                lambda: ClassLesson.objects.create(
                    settings=self.settings,
                    school_class=self.school_class,
                    weekday=2,
                    period_index=2,
                    subject=self.subject,
                    teacher=self.teacher,
                ),
            ),
            (
                "school_class",
                lambda: (
                    setattr(self.school_class, "name", "الأول ب"),
                    self.school_class.save(),
                ),
            ),
            (
                "subject",
                lambda: (
                    setattr(self.subject, "name", "علوم"),
                    self.subject.save(),
                ),
            ),
            (
                "teacher",
                lambda: (
                    setattr(self.teacher, "name", "محمد"),
                    self.teacher.save(),
                ),
            ),
            (
                "duty",
                lambda: DutyAssignment.objects.create(
                    school=self.school,
                    date=timezone.localdate(),
                    teacher_name="معلم مناوبة",
                    duty_type=DutyAssignment.DUTY_DUTY,
                ),
            ),
            (
                "announcement",
                lambda: Announcement.objects.create(
                    school=self.school,
                    title="تنبيه",
                    body="اختبار التحديث",
                ),
            ),
            (
                "excellence",
                lambda: Excellence.objects.create(
                    school=self.school,
                    teacher_name="معلم متميز",
                    reason="نشاط صفي",
                ),
            ),
            (
                "school",
                lambda: (
                    setattr(self.school, "name", "مدرسة التحديثات الجديدة"),
                    self.school.save(),
                ),
            ),
        ]

        for label, mutate in sources:
            with self.subTest(source=label):
                self._assert_dashboard_source_change_refreshes_display(mutate)


class SnapshotPayloadBuildTests(SimpleTestCase):
    def _fake_settings(self):
        return SimpleNamespace(
            school=None,
            school_id=7,
            schedule_revision=12,
            theme="indigo",
            featured_panel="excellence",
            refresh_interval_sec=60,
            standby_scroll_speed=0.8,
            periods_scroll_speed=0.5,
            display_accent_color="",
            get_display_before_title=lambda: "قبل الدوام",
            get_display_before_badge=lambda: "أهلا بكم",
            get_display_after_title=lambda: "بعد الدوام",
            get_display_after_badge=lambda: "أحسنتم",
            get_display_after_holiday_title=lambda: "إجازة سعيدة",
            get_display_after_holiday_badge=lambda: "إجازة",
            get_display_holiday_title=lambda: "اليوم إجازة",
            get_display_holiday_badge=lambda: "إجازة",
        )

    def test_steady_snapshot_merges_dashboard_content(self):
        day_snap = {
            "now": timezone.now().isoformat(),
            "meta": {
                "date": "2026-04-20",
                "weekday": 1,
                "is_school_day": True,
                "is_active_window": False,
            },
            "settings": {"refresh_interval_sec": 60},
            "state": {"type": "off", "reason": "after_hours"},
            "day_path": [],
            "period_classes": [],
            "period_classes_map": {},
            "standby": [],
            "excellence": [],
            "announcements": [],
        }

        def merge_content(_request, snap, _settings_obj):
            snap["announcements"] = [{"message": "تنبيه مباشر"}]

        request = RequestFactory().get("/api/display/snapshot/")
        with (
            patch.object(av, "_call_build_day_snapshot", return_value=day_snap),
            patch.object(av, "_merge_real_data_into_snapshot", side_effect=merge_content) as merge_real,
            patch.object(av, "_build_period_classes_map", return_value={}),
            patch.object(av, "_metrics_incr", lambda *args, **kwargs: None),
            patch.object(av, "_metrics_add", lambda *args, **kwargs: None),
            patch.object(av, "_metrics_set_max", lambda *args, **kwargs: None),
            patch.object(av, "_metrics_log_maybe", lambda *args, **kwargs: None),
        ):
            snap, _build_ms = av._build_snapshot_payload(
                request,
                self._fake_settings(),
                school_id=7,
                rev=12,
            )

        merge_real.assert_called_once()
        self.assertEqual(snap["state"]["type"], "OFF_HOURS")
        self.assertEqual(snap["announcements"], [{"message": "تنبيه مباشر"}])


class SnapshotObservabilityTests(SimpleTestCase):
    def test_snapshot_cache_hit_metrics_survive_log_sampling(self):
        metric_keys: list[str] = []

        with patch.object(so, "metric_incr", side_effect=lambda key, delta=1, ttl=so.METRIC_TTL_SEC: metric_keys.append(key)):
            with patch.object(so, "_should_sample_log", return_value=False):
                with patch.object(so, "log_event") as log_event:
                    so.observe_snapshot_cache(
                        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
                        outcome="hit",
                        layer="steady",
                        school_id=7,
                        rev=42,
                        day_key="2026-04-23",
                    )

        self.assertIn("metrics:snapshot_cache:hit", metric_keys)
        log_event.assert_not_called()

    def test_snapshot_metrics_payload_groups_new_counters(self):
        values = {
            "metrics:snapshot_cache:hit": 4,
            "metrics:snapshot_cache:miss": 2,
            "metrics:snapshot_cache:revision_reject": 1,
            "metrics:snapshot_cache:wake_boundary_reject": 3,
            "metrics:snapshot_build:count": 5,
            "metrics:snapshot_build:soft_timeout": 1,
            "metrics:snapshot_build:duration_ms:sum": 500,
            "metrics:snapshot_build:duration_ms:max": 200,
            "metrics:snapshot_build:source:inline:count": 2,
            "metrics:snapshot_build:source:queue:count": 2,
            "metrics:snapshot_build:source:stale:count": 1,
            "metrics:snapshot_queue:enqueue_count": 6,
            "metrics:snapshot_queue:skipped_enqueue": 4,
            "metrics:snapshot_queue:deduplicated_jobs": 3,
            "metrics:snapshot_queue:queue_wait_time_ms:sum": 1200,
            "metrics:snapshot_queue:queue_wait_time_ms:max": 700,
            "metrics:snapshot_queue:queue_wait_time_ms:count": 4,
        }

        with patch.object(so, "metric_get_int", side_effect=lambda key: int(values.get(key, 0))):
            payload = so.snapshot_metrics_payload()

        self.assertEqual(payload["snapshot_cache"]["hit"], 4)
        self.assertEqual(payload["snapshot_cache"]["miss"], 2)
        self.assertEqual(payload["snapshot_build"]["count"], 5)
        self.assertEqual(payload["snapshot_build"]["soft_timeout"], 1)
        self.assertEqual(payload["snapshot_build"]["duration_ms_avg"], 100)
        self.assertEqual(payload["snapshot_build"]["source"]["inline"]["count"], 2)
        self.assertEqual(payload["queue"]["enqueue_count"], 6)
        self.assertEqual(payload["queue"]["queue_wait_time_ms_avg"], 300)

    @override_settings(DEBUG=True)
    def test_metrics_endpoint_includes_snapshot_observability(self):
        request = RequestFactory().get("/api/display/metrics/")
        fake_payload = {"snapshot_cache": {"hit": 1, "miss": 0}}
        fake_cache = SimpleNamespace(get=lambda key: 0)
        with patch.object(av, "cache", fake_cache):
            with patch.object(av, "_snapshot_metrics_payload", return_value=fake_payload):
                response = av.metrics(request)
        data = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertIn("snapshot_observability", data)
        self.assertIn("snapshot_cache", data["snapshot_observability"])


class SnapshotWorkerCommandTests(SimpleTestCase):
    def _make_command(self):
        command = SnapshotWorkerCommand()
        command.stdout = io.StringIO()
        return command

    def test_worker_skips_stale_job_when_newer_pending_job_exists(self):
        job = {
            "school_id": 7,
            "rev": 12,
            "day_key": "2026-04-20",
            "job_id": "job-1",
            "queued_at": 1.0,
            "_dequeued_at": 2.0,
            "_queue_wait_ms": 1000,
        }
        command = self._make_command()
        with (
            patch("schedule.management.commands.display_snapshot_worker.snapshot_queue_available", return_value=True),
            patch("schedule.management.commands.display_snapshot_worker.touch_snapshot_worker_heartbeat"),
            patch("schedule.management.commands.display_snapshot_worker.dequeue_snapshot_build", return_value=job),
            patch("schedule.management.commands.display_snapshot_worker.get_latest_snapshot_revision", return_value=13),
            patch("schedule.management.commands.display_snapshot_worker.get_pending_snapshot_job_id", return_value="job-2"),
            patch("schedule.management.commands.display_snapshot_worker.materialize_snapshot_for_school") as materialize,
            patch("schedule.management.commands.display_snapshot_worker.complete_snapshot_job") as complete_job,
        ):
            command.handle(once=True, poll_timeout=0, idle_sleep=0.5, max_idle_sleep=2.0)

        materialize.assert_not_called()
        complete_job.assert_called_once_with(job)
        self.assertIn("event=skip_stale", command.stdout.getvalue())

    def test_worker_skips_when_snapshot_is_already_cached(self):
        job = {
            "school_id": 7,
            "rev": 15,
            "day_key": "2026-04-20",
            "job_id": "job-1",
            "queued_at": 1.0,
            "_dequeued_at": 2.0,
            "_queue_wait_ms": 1000,
        }
        command = self._make_command()
        with (
            patch("schedule.management.commands.display_snapshot_worker.snapshot_queue_available", return_value=True),
            patch("schedule.management.commands.display_snapshot_worker.touch_snapshot_worker_heartbeat"),
            patch("schedule.management.commands.display_snapshot_worker.dequeue_snapshot_build", return_value=job),
            patch("schedule.management.commands.display_snapshot_worker.get_latest_snapshot_revision", return_value=15),
            patch("schedule.management.commands.display_snapshot_worker.get_pending_snapshot_job_id", return_value="job-1"),
            patch("schedule.management.commands.display_snapshot_worker.acquire_snapshot_job_lock", return_value=(True, "lock-key", "job-1")),
            patch("schedule.management.commands.display_snapshot_worker.get_materialized_snapshot_revision", return_value=None),
            patch("schedule.management.commands.display_snapshot_worker.get_cached_snapshot_revision", return_value=15),
            patch("schedule.management.commands.display_snapshot_worker.release_snapshot_job_lock") as release_lock,
            patch("schedule.management.commands.display_snapshot_worker.materialize_snapshot_for_school") as materialize,
            patch("schedule.management.commands.display_snapshot_worker.complete_snapshot_job") as complete_job,
        ):
            command.handle(once=True, poll_timeout=0, idle_sleep=0.5, max_idle_sleep=2.0)

        materialize.assert_not_called()
        release_lock.assert_called_once_with(lock_key="lock-key", token="job-1")
        complete_job.assert_called_once_with(job)
        self.assertIn("event=skip_cached", command.stdout.getvalue())

    def test_worker_continues_and_logs_errors_for_bad_job(self):
        job = {
            "school_id": 7,
            "rev": 15,
            "day_key": "2026-04-20",
            "job_id": "job-1",
            "queued_at": 1.0,
            "_dequeued_at": 2.0,
            "_queue_wait_ms": 1000,
        }
        command = self._make_command()
        with (
            patch("schedule.management.commands.display_snapshot_worker.snapshot_queue_available", return_value=True),
            patch("schedule.management.commands.display_snapshot_worker.touch_snapshot_worker_heartbeat"),
            patch("schedule.management.commands.display_snapshot_worker.dequeue_snapshot_build", return_value=job),
            patch("schedule.management.commands.display_snapshot_worker.get_latest_snapshot_revision", return_value=15),
            patch("schedule.management.commands.display_snapshot_worker.get_pending_snapshot_job_id", return_value="job-1"),
            patch("schedule.management.commands.display_snapshot_worker.acquire_snapshot_job_lock", return_value=(True, "lock-key", "job-1")),
            patch("schedule.management.commands.display_snapshot_worker.get_materialized_snapshot_revision", return_value=None),
            patch("schedule.management.commands.display_snapshot_worker.get_cached_snapshot_revision", return_value=None),
            patch("schedule.management.commands.display_snapshot_worker.materialize_snapshot_for_school", side_effect=RuntimeError("boom")),
            patch("schedule.management.commands.display_snapshot_worker.release_snapshot_job_lock"),
            patch("schedule.management.commands.display_snapshot_worker.complete_snapshot_job") as complete_job,
        ):
            command.handle(once=True, poll_timeout=0, idle_sleep=0.5, max_idle_sleep=2.0)

        complete_job.assert_called_once_with(job)
        self.assertIn("event=errors", command.stdout.getvalue())

    def test_worker_lock_prevents_duplicate_processing(self):
        job = {
            "school_id": 7,
            "rev": 15,
            "day_key": "2026-04-20",
            "job_id": "job-1",
            "queued_at": 1.0,
            "_dequeued_at": 2.0,
            "_queue_wait_ms": 1000,
        }
        command = self._make_command()
        with (
            patch("schedule.management.commands.display_snapshot_worker.snapshot_queue_available", return_value=True),
            patch("schedule.management.commands.display_snapshot_worker.touch_snapshot_worker_heartbeat"),
            patch("schedule.management.commands.display_snapshot_worker.dequeue_snapshot_build", return_value=job),
            patch("schedule.management.commands.display_snapshot_worker.get_latest_snapshot_revision", return_value=15),
            patch("schedule.management.commands.display_snapshot_worker.get_pending_snapshot_job_id", return_value="job-1"),
            patch("schedule.management.commands.display_snapshot_worker.acquire_snapshot_job_lock", return_value=(False, "lock-key", "job-1")),
            patch("schedule.management.commands.display_snapshot_worker.materialize_snapshot_for_school") as materialize,
            patch("schedule.management.commands.display_snapshot_worker.complete_snapshot_job") as complete_job,
        ):
            command.handle(once=True, poll_timeout=0, idle_sleep=0.5, max_idle_sleep=2.0)

        materialize.assert_not_called()
        complete_job.assert_called_once_with(job)
        self.assertIn("event=job_locked_or_skipped", command.stdout.getvalue())


@override_settings(DEBUG=False)
class WebSocketMetricsAccessTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_ws_metrics_is_hidden_in_production_without_a_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISPLAY_WS_METRICS_KEY", None)
            os.environ.pop("DISPLAY_METRICS_KEY", None)
            response = av.ws_metrics(self.factory.get("/api/display/ws-metrics/"))

        self.assertEqual(response.status_code, 404)

    def test_ws_metrics_rejects_an_invalid_key(self):
        with patch.dict(os.environ, {"DISPLAY_WS_METRICS_KEY": "correct-key"}, clear=False):
            response = av.ws_metrics(
                self.factory.get(
                    "/api/display/ws-metrics/",
                    HTTP_X_DISPLAY_METRICS_KEY="wrong-key",
                )
            )

        self.assertEqual(response.status_code, 403)

    def test_ws_metrics_accepts_the_configured_key(self):
        with patch.dict(os.environ, {"DISPLAY_WS_METRICS_KEY": "correct-key"}, clear=False):
            response = av.ws_metrics(
                self.factory.get(
                    "/api/display/ws-metrics/",
                    HTTP_X_DISPLAY_METRICS_KEY="correct-key",
                )
            )

        self.assertEqual(response.status_code, 200)

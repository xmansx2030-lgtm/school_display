from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings

from core.display_presence import display_live_threshold_seconds
from display.consumers import DisplayConsumer, DisplayPreviewConsumer, _presence_touch_interval_seconds


class DisplayClientTimingRegressionTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = (settings.BASE_DIR / "static" / "js" / "display.js").read_text(encoding="utf-8")
        cls.template = (settings.BASE_DIR / "templates" / "website" / "display.html").read_text(encoding="utf-8")

    def test_cached_snapshot_time_cannot_override_fresh_response_header(self):
        self.assertIn('lastServerHeaderSyncAt', self.source)
        self.assertIn('!hasFreshHeaderSync', self.source)
        self.assertIn('applyServerNowMs(h, "header")', self.source)

    def test_bell_uses_preloaded_audio_and_exact_boundary_timer(self):
        self.assertIn('scheduleNextBoundaryBell()', self.source)
        self.assertIn('"bell_boundary_exact"', self.source)
        self.assertNotIn('audio.load();\n      audio.play()', self.source)

    def test_screen_wake_lock_is_reacquired_after_release_and_visibility_change(self):
        self.assertIn('navigator.wakeLock.request("screen")', self.source)
        self.assertIn('sentinel.addEventListener("release"', self.source)
        self.assertIn('_scheduleWakeLockRetry(1000);', self.source)
        self.assertIn('document.addEventListener("visibilitychange"', self.source)
        self.assertIn('_stopWakeLockKeepalive();', self.source)

    def test_emergency_overlay_expires_without_waiting_for_a_new_snapshot(self):
        emergency_source = self.source.split(
            "function renderEmergencyAlerts", 1
        )[1].split("function renderOfflineMode", 1)[0]

        self.assertIn('Date.parse(String(item.expires_at))', emergency_source)
        self.assertIn('if (expiresMs <= currentMs) continue;', emergency_source)
        self.assertIn('setNamedTimer("emergency_boundary"', emergency_source)
        self.assertIn('renderEmergencyAlerts(list);', emergency_source)

    def test_display_does_not_reserve_a_generic_subtitle_slot(self):
        self.assertNotIn("heroSubtitle", self.source)
        self.assertNotIn('id="heroSubtitle"', self.template)
        self.assertNotIn("متابعة مباشرة للحصة والنشاط الحالي", self.source)

    def test_preview_uses_manager_session_and_a_non_binding_realtime_channel(self):
        self.assertIn('credentials: IS_PREVIEW ? "same-origin" : "omit"', self.source)
        self.assertIn('IS_PREVIEW ? "/ws/display-preview/" : "/ws/display/"', self.source)
        self.assertNotIn('ws_skipped_in_preview', self.source)

    def test_device_binding_notice_is_actionable_and_does_not_expose_the_url(self):
        self.assertIn("هذه الشاشة مفعّلة على جهاز آخر", self.template)
        self.assertIn('id="blockerRetryBtn"', self.template)
        self.assertIn("فك ارتباط الجهاز", self.template)
        blocker_source = self.source.split("function showBlocker", 1)[1].split("function stopPolling", 1)[0]
        self.assertNotIn("window.location.pathname", blocker_source)
        self.assertNotIn("window.location.search", blocker_source)

    def test_device_binding_notice_supports_specific_blocker_states(self):
        self.assertIn('data-kind="binding"', self.template)
        self.assertIn('blockerKind === "device"', self.source)
        self.assertIn('showBlocker(ui.title, ui.details, ui.kind);', self.source)

    def test_local_schedule_transitions_refresh_all_board_state_copy(self):
        day_block_source = self.source.split(
            "function dayEngineApplyBlock", 1
        )[1].split("function dayEngineOnZero", 1)[0]
        day_over_source = self.source.split(
            "function dayEngineApplyDayOver", 1
        )[1].split("function dayEngineSyncToLocalNow", 1)[0]
        optimistic_source = self.source.split(
            "function optimisticAdvanceToNextBlock", 1
        )[1].split("function onCountdownZero", 1)[0]

        self.assertIn(
            "applyBoardStatePresentation(stType, rem > 0);",
            day_block_source,
        )
        self.assertIn(
            'applyBoardStatePresentation("after", false);',
            day_over_source,
        )
        self.assertIn(
            "applyBoardStatePresentation(stType, rem > 0);",
            optimistic_source,
        )

    def test_test_runtime_uses_isolated_in_memory_services(self):
        self.assertEqual(
            settings.CACHES["default"]["BACKEND"],
            "django.core.cache.backends.locmem.LocMemCache",
        )
        self.assertEqual(
            settings.CHANNEL_LAYERS["default"]["BACKEND"],
            "channels.layers.InMemoryChannelLayer",
        )


class DisplayConsumerEventTests(SimpleTestCase):
    def test_client_ping_refreshes_display_presence(self):
        consumer = DisplayConsumer()
        consumer.screen = SimpleNamespace(id=10, pk=10, school_id=7)
        consumer.channel_name = "test-channel"
        consumer._touch_presence = AsyncMock()
        sent = []

        async def fake_send(*, text_data=None, bytes_data=None):
            sent.append(text_data)

        consumer.send = fake_send

        async_to_sync(consumer.receive)(text_data='{"type":"ping"}')

        consumer._touch_presence.assert_awaited_once()
        self.assertIn('"type": "pong"', sent[0])

    def test_broadcast_invalidate_emits_snapshot_refresh_event(self):
        consumer = DisplayConsumer()
        consumer.screen = SimpleNamespace(id=10, school_id=7)
        sent = []

        async def fake_send(*, text_data=None, bytes_data=None):
            sent.append(text_data)

        consumer.send = fake_send

        async_to_sync(consumer.broadcast_invalidate)(
            {"revision": 42, "school_id": 7, "reason": "content_changed"}
        )

        self.assertEqual(len(sent), 1)
        self.assertIn('"type": "snapshot_refresh"', sent[0])
        self.assertIn('"revision": 42', sent[0])
        self.assertIn('"reason": "content_changed"', sent[0])

    def test_broadcast_invalidate_keeps_school_isolation(self):
        consumer = DisplayConsumer()
        consumer.screen = SimpleNamespace(id=10, school_id=7)
        sent = []

        async def fake_send(*, text_data=None, bytes_data=None):
            sent.append(text_data)

        consumer.send = fake_send

        async_to_sync(consumer.broadcast_invalidate)(
            {"revision": 42, "school_id": 8, "reason": "content_changed"}
        )

        self.assertEqual(sent, [])

    def test_preview_receives_invalidation_without_counting_as_a_display_broadcast(self):
        consumer = DisplayPreviewConsumer()
        consumer.screen = SimpleNamespace(id=10, school_id=7)
        sent = []

        async def fake_send(*, text_data=None, bytes_data=None):
            sent.append(text_data)

        consumer.send = fake_send

        with patch("display.consumers.ws_metrics.broadcast_sent") as metric, patch(
            "display.consumers._ws_metric_incr"
        ) as shared_metric:
            async_to_sync(consumer.broadcast_invalidate)(
                {"revision": 43, "school_id": 7, "reason": "content_changed"}
            )

        self.assertIn('"revision": 43', sent[0])
        metric.assert_not_called()
        shared_metric.assert_not_called()

    def test_targeted_commands_do_not_reach_another_screen(self):
        consumer = DisplayConsumer()
        consumer.screen = SimpleNamespace(id=10, school_id=7)
        sent = []

        async def fake_send(*, text_data=None, bytes_data=None):
            sent.append(text_data)

        consumer.send = fake_send

        async_to_sync(consumer.broadcast_invalidate)(
            {
                "revision": 42,
                "school_id": 7,
                "target_screen_id": 11,
                "reason": "manual_refresh",
            }
        )
        async_to_sync(consumer.broadcast_reload)(
            {"school_id": 7, "target_screen_id": 11}
        )

        self.assertEqual(sent, [])

    def test_long_lived_connection_renews_its_group_memberships(self):
        consumer = DisplayConsumer()
        consumer.screen = SimpleNamespace(id=10, school_id=7)
        consumer.channel_name = "test-channel"
        consumer.school_group_name = "school_7"
        consumer.token_group_name = "token_0123456789abcdef"
        consumer.channel_layer = SimpleNamespace(group_add=AsyncMock())

        async_to_sync(consumer._refresh_group_memberships)()

        self.assertEqual(consumer.channel_layer.group_add.await_count, 2)
        consumer.channel_layer.group_add.assert_any_await("school_7", "test-channel")
        consumer.channel_layer.group_add.assert_any_await(
            "token_0123456789abcdef", "test-channel"
        )
        self.assertGreater(consumer._last_group_refresh_at, 0)


class DashboardPreviewWebSocketTests(TransactionTestCase):
    """The realtime preview is session-authenticated and never owns the TV slot."""

    def setUp(self):
        from core.models import DisplayScreen, School, UserProfile
        from django.contrib.auth import get_user_model

        self.school = School.objects.create(name="مدرسة البث الحي", slug="preview-ws-school")
        self.screen = DisplayScreen.objects.create(
            school=self.school,
            name="الشاشة الرئيسية",
            is_active=True,
        )
        self.manager = get_user_model().objects.create_user(
            username="preview_ws_manager",
            password="StrongPass123!",
        )
        profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        profile.schools.add(self.school)

    def test_authorised_preview_socket_connects_without_binding_or_presence(self):
        from channels.layers import get_channel_layer
        from channels.testing import WebsocketCommunicator
        from config.asgi import application
        from core.display_presence import display_is_live
        from display.ws_groups import school_group_name

        self.client.force_login(self.manager)
        session_cookie = self.client.cookies["sessionid"].value

        async def scenario():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/display-preview/?token={self.screen.token}",
                headers=[
                    (b"origin", b"http://testserver"),
                    (b"cookie", f"sessionid={session_cookie}".encode()),
                ],
            )
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.send_json_to({"type": "ping"})
            self.assertEqual((await communicator.receive_json_from())["type"], "pong")
            await get_channel_layer().group_send(
                school_group_name(self.school.pk),
                {
                    "type": "broadcast_invalidate",
                    "school_id": self.school.pk,
                    "revision": 91,
                    "reason": "content_changed",
                },
            )
            update = await communicator.receive_json_from()
            self.assertEqual(update["type"], "snapshot_refresh")
            self.assertEqual(update["revision"], 91)
            await communicator.disconnect()

        async_to_sync(scenario)()
        self.screen.refresh_from_db()
        self.assertFalse(self.screen.bound_device_id)
        self.assertFalse(display_is_live(self.screen))

    def test_anonymous_preview_socket_is_rejected(self):
        from channels.testing import WebsocketCommunicator
        from config.asgi import application

        async def scenario():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/display-preview/?token={self.screen.token}",
                headers=[(b"origin", b"http://testserver")],
            )
            connected, close_code = await communicator.connect()
            self.assertFalse(connected)
            self.assertEqual(close_code, 4403)

        async_to_sync(scenario)()


class PresenceTouchThrottleTests(SimpleTestCase):
    """The ping loop must not turn into a presence write loop.

    Pings run every 20s because that is what holds the socket open through
    school proxies. Presence only has to stay inside the dashboard's live
    threshold (120s), and each refresh costs a thread hop plus cache writes for
    every connected screen in the fleet.
    """

    def _consumer(self):
        consumer = DisplayConsumer()
        consumer.screen = SimpleNamespace(pk=1, id=1, school_id=7)
        return consumer

    @override_settings(DISPLAY_PRESENCE_TOUCH_INTERVAL_SEC=60)
    def test_first_touch_after_connecting_always_runs(self):
        consumer = self._consumer()

        self.assertTrue(consumer._presence_touch_is_due(1000.0))

    @override_settings(DISPLAY_PRESENCE_TOUCH_INTERVAL_SEC=60)
    def test_pings_inside_the_interval_do_not_touch_presence(self):
        consumer = self._consumer()
        consumer._presence_touch_is_due(1000.0)

        # The next two pings of the 20s loop fall inside the 60s interval.
        self.assertFalse(consumer._presence_touch_is_due(1020.0))
        self.assertFalse(consumer._presence_touch_is_due(1040.0))

    @override_settings(DISPLAY_PRESENCE_TOUCH_INTERVAL_SEC=60)
    def test_presence_is_refreshed_once_the_interval_elapses(self):
        consumer = self._consumer()
        consumer._presence_touch_is_due(1000.0)

        self.assertTrue(consumer._presence_touch_is_due(1060.0))
        # ...and the window restarts from there.
        self.assertFalse(consumer._presence_touch_is_due(1080.0))

    @override_settings(DISPLAY_PRESENCE_TOUCH_INTERVAL_SEC=60)
    def test_the_interval_stays_inside_the_dashboard_live_threshold(self):
        """A screen must never look offline to its manager while it is connected."""
        interval = _presence_touch_interval_seconds()

        self.assertLessEqual(interval, display_live_threshold_seconds() / 2)


class PingMetricBatchingTests(SimpleTestCase):
    """Counting pings must stay exact while costing far fewer round trips.

    `_ws_metric_incr` runs on the event loop that also delivers every broadcast,
    so one increment per ping per screen is the wrong place to spend it. The
    batch changes when the counter is written, never what it totals.
    """

    def test_pending_pings_are_flushed_by_their_full_count(self):
        consumer = DisplayConsumer()
        consumer._pending_ping_metric = 7
        calls = []

        with patch("display.consumers._ws_metric_incr", side_effect=lambda name, delta=1: calls.append((name, delta))):
            consumer._flush_ping_metric()

        self.assertEqual(calls, [("server_ping_sent", 7)])
        self.assertEqual(consumer._pending_ping_metric, 0)

    def test_flushing_nothing_writes_nothing(self):
        consumer = DisplayConsumer()
        calls = []

        with patch("display.consumers._ws_metric_incr", side_effect=lambda name, delta=1: calls.append((name, delta))):
            consumer._flush_ping_metric()

        self.assertEqual(calls, [])

    def test_a_closing_connection_does_not_lose_its_count(self):
        """Cancellation lands while the loop sleeps, outside its own try block."""
        consumer = DisplayConsumer()
        consumer._pending_ping_metric = 3
        calls = []

        with patch("display.consumers._ws_metric_incr", side_effect=lambda name, delta=1: calls.append((name, delta))):
            async_to_sync(consumer._stop_server_ping_task)()

        self.assertEqual(calls, [("server_ping_sent", 3)])


class DashboardPreviewNeverClaimsTheScreenTests(TestCase):
    """فتح المعاينة من اللوحة يجب ألا يسحب الشاشة من التلفاز.

    زرّا «معاينة» و«فتح المعاينة» يفتحان رابط العرض نفسه الذي يفتحه التلفاز.
    وبما أن أول جهاز يفتحه يحجز ``bound_device_id``، كان متصفح المدير يفوز
    بالمكان فيُقابَل التلفاز برسالة «هذه الشاشة مفعّلة على جهاز آخر» — وهو أكثر
    ما يعطّل المدرسة في يومها الأول.
    """

    def setUp(self):
        from core.models import DisplayScreen, School, SubscriptionPlan, UserProfile
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from schedule.models import SchoolSettings
        from subscriptions.models import SchoolSubscription

        self.school = School.objects.create(name="مدرسة المعاينة", slug="preview-school")
        SchoolSettings.objects.create(school=self.school, name=self.school.name)
        plan = SubscriptionPlan.objects.create(
            code="preview-plan", name="خطة", price=100, duration_days=365, max_screens=3
        )
        SchoolSubscription.objects.create(
            school=self.school, plan=plan, starts_at=timezone.localdate(), status="active"
        )
        self.screen = DisplayScreen.objects.create(
            school=self.school, name="الشاشة الرئيسية", is_active=True
        )
        self.manager = get_user_model().objects.create_user(
            username="preview_mgr", password="StrongPass123!"
        )
        profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        profile.schools.add(self.school)

    def _snapshot(self, device, *, preview=False):
        headers = {
            "HTTP_X_DISPLAY_TOKEN": self.screen.token,
            "HTTP_X_DISPLAY_DEVICE": device,
        }
        if preview:
            headers["HTTP_X_DISPLAY_PREVIEW"] = "1"
        return self.client.get(
            "/api/display/snapshot/",
            {"token": self.screen.token, "dk": device},
            **headers,
        )

    def test_manager_preview_leaves_the_device_slot_free_for_the_tv(self):
        self.client.force_login(self.manager)
        self.assertEqual(self._snapshot("manager-laptop", preview=True).status_code, 200)

        self.screen.refresh_from_db()
        self.assertFalse(self.screen.bound_device_id)

        self.client.logout()
        self.assertEqual(self._snapshot("tv-device").status_code, 200)

        self.screen.refresh_from_db()
        self.assertEqual(self.screen.bound_device_id, "tv-device")

    def test_preview_keeps_working_while_the_tv_owns_the_screen(self):
        self.assertEqual(self._snapshot("tv-device").status_code, 200)

        self.client.force_login(self.manager)
        self.assertEqual(self._snapshot("manager-laptop", preview=True).status_code, 200)

        self.screen.refresh_from_db()
        self.assertEqual(self.screen.bound_device_id, "tv-device")

    def test_preview_does_not_report_the_screen_as_live(self):
        from core.display_presence import display_is_live

        self.client.force_login(self.manager)
        self._snapshot("manager-laptop", preview=True)

        self.screen.refresh_from_db()
        self.assertFalse(display_is_live(self.screen))

    def test_the_flag_alone_grants_nothing_to_a_token_holder(self):
        self.assertEqual(self._snapshot("tv-device").status_code, 200)

        response = self._snapshot("stranger-device", preview=True)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "screen_bound")

    def test_a_manager_of_another_school_gets_no_preview(self):
        from core.models import School, UserProfile
        from django.contrib.auth import get_user_model

        other = School.objects.create(name="مدرسة أخرى", slug="preview-other")
        stranger = get_user_model().objects.create_user(
            username="other_mgr", password="StrongPass123!"
        )
        profile = UserProfile.objects.create(user=stranger, active_school=other)
        profile.schools.add(other)

        self.assertEqual(self._snapshot("tv-device").status_code, 200)
        self.client.force_login(stranger)

        self.assertEqual(self._snapshot("stranger-device", preview=True).status_code, 403)

    def test_preview_mode_is_only_rendered_for_an_authorised_manager(self):
        url = f"/s/{self.screen.short_code}/?preview=1"

        self.client.force_login(self.manager)
        response = self.client.get(url)
        self.assertContains(response, 'data-preview-mode="1"')
        self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")

        self.client.logout()
        self.assertNotContains(self.client.get(url), 'data-preview-mode="1"')

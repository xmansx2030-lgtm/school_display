from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.conf import settings
from django.test import SimpleTestCase

from display.consumers import DisplayConsumer


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

    def test_display_does_not_reserve_a_generic_subtitle_slot(self):
        self.assertNotIn("heroSubtitle", self.source)
        self.assertNotIn('id="heroSubtitle"', self.template)
        self.assertNotIn("متابعة مباشرة للحصة والنشاط الحالي", self.source)

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

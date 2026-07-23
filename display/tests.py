from types import SimpleNamespace
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase

from display.consumers import DisplayConsumer


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

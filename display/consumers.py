"""
WebSocket consumer for display screens.

Phase 1 (Server-Side Readiness):
    - Token validation (query param: token=xxx)
    - Device binding enforcement (dk required)
    - Tenant isolation (server-derived group: school_<id>)
    - Broadcast handler for invalidation messages
    - Ping/pong keepalive

Phase 2 (Dark Launch):
    - Clients may connect (optional), but polling remains active
    
Security:
    - Token is sole source of identity
    - No client-supplied school_id accepted
    - Server derives group from screen.school_id only
    - dk binding prevents multi-device access (respects DISPLAY_ALLOW_MULTI_DEVICE)
"""

import asyncio
import json
import logging
import time
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from django.core.cache import cache

from display.services import (
    ScreenBoundError,
    ScreenNotFoundError,
    bind_device_atomic,
)
from display.ws_cluster_metrics import heartbeat as ws_cluster_heartbeat
from display.ws_cluster_metrics import register_connect as ws_cluster_register_connect
from display.ws_cluster_metrics import register_disconnect as ws_cluster_register_disconnect
from display.ws_groups import school_group_name, token_group_name
from display.ws_metrics import ws_metrics
from core.display_presence import touch_display_presence
from core.screen_diagnostics import record_disconnect_signal

logger = logging.getLogger(__name__)


def _ws_log_interval_seconds() -> int:
    try:
        v = int(getattr(settings, "WS_METRICS_LOG_INTERVAL", 300) or 300)
    except Exception:
        v = 300
    return max(30, min(3600, v))


def _should_log_ws_event(kind: str, *, screen_id: int | None = None, code: int | None = None) -> bool:
    parts = [str(kind or "ws")]
    if screen_id is not None:
        parts.append(str(int(screen_id)))
    if code is not None:
        parts.append(str(int(code)))
    key = "display:ws_log:" + ":".join(parts)
    try:
        return bool(cache.add(key, "1", timeout=_ws_log_interval_seconds()))
    except Exception:
        return True


def _ws_metric_ttl_seconds() -> int:
    return 60 * 60 * 24


def _ws_ping_interval_seconds() -> int:
    try:
        v = int(getattr(settings, "WS_PING_INTERVAL_SECONDS", 20) or 20)
    except Exception:
        v = 20
    return max(10, min(120, v))


def _ws_group_refresh_interval_seconds() -> int:
    """Refresh Redis group membership before Channels expires it."""
    try:
        v = int(getattr(settings, "WS_GROUP_REFRESH_INTERVAL_SECONDS", 6 * 60 * 60) or 6 * 60 * 60)
    except Exception:
        v = 6 * 60 * 60
    return max(60, min(12 * 60 * 60, v))


def _ws_metric_incr(name: str) -> None:
    key = f"metrics:ws:{str(name or '').strip()}"
    try:
        cache.add(key, 0, timeout=_ws_metric_ttl_seconds())
    except Exception:
        pass
    try:
        cache.incr(key)
    except Exception:
        try:
            cur = int(cache.get(key) or 0)
            cache.set(key, cur + 1, timeout=_ws_metric_ttl_seconds())
        except Exception:
            pass


class DisplayConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for realtime display screen updates.
    
    Connection URL: ws://domain/ws/display/?token=<token>&dk=<device_id>
    
    Lifecycle:
        1. connect() → validate token + bind device → join school group
        2. receive() → handle ping/pong keepalive
        3. broadcast_invalidate() → send {type: "snapshot_refresh", revision: X}
        4. disconnect() → leave group
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.screen = None
        self.school_group_name = None
        self.token_group_name = None
        self.device_id = None
        self.token_value = None
        self._server_ping_task = None
        self._last_group_refresh_at = 0.0

    def _start_server_ping_task(self) -> None:
        if self._server_ping_task and not self._server_ping_task.done():
            return
        self._server_ping_task = asyncio.create_task(self._server_ping_loop())

    async def _touch_presence(self) -> None:
        if not self.screen or not getattr(self.screen, "pk", None):
            return
        try:
            from channels.db import database_sync_to_async

            await database_sync_to_async(touch_display_presence)(
                int(self.screen.pk),
                token=self.token_value,
            )
        except Exception:
            pass

    async def _stop_server_ping_task(self) -> None:
        task = self._server_ping_task
        self._server_ping_task = None
        if not task:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _server_ping_loop(self) -> None:
        interval = _ws_ping_interval_seconds()
        while True:
            await asyncio.sleep(interval)
            try:
                if time.monotonic() - self._last_group_refresh_at >= _ws_group_refresh_interval_seconds():
                    await self._refresh_group_memberships()
                await self.send(text_data=json.dumps({"type": "heartbeat"}))
                await self._touch_presence()
                _ws_metric_incr("server_ping_sent")
            except asyncio.CancelledError:
                raise
            except Exception:
                _ws_metric_incr("server_ping_send_failed")
                break

    async def _refresh_group_memberships(self) -> None:
        """Keep long-lived TV sockets addressable by dashboard broadcasts."""
        if not self.channel_layer or not self.channel_name or not self.school_group_name:
            return
        try:
            await self.channel_layer.group_add(self.school_group_name, self.channel_name)
            if self.token_group_name:
                await self.channel_layer.group_add(self.token_group_name, self.channel_name)
            self._last_group_refresh_at = time.monotonic()
        except Exception:
            screen_id = int(self.screen.id) if self.screen else 0
            if _should_log_ws_event("group_refresh_failed", screen_id=screen_id):
                logger.exception("WS group membership refresh failed for screen %s", screen_id or "?")
    
    async def connect(self):
        """
        Validate token + device binding, then join school group.
        
        Close codes:
            4400: Missing token or dk
            4403: Token invalid or screen inactive
            4408: Screen bound to different device
        """
        # Parse query params
        query_string = self.scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        
        token = query_params.get("token", [None])[0]
        self.token_value = token
        self.device_id = query_params.get("dk", [None])[0]
        
        # Validate required params
        if not token or not self.device_id:
            logger.warning("WS connect rejected: missing token or dk")
            ws_metrics.connection_failed()
            _ws_metric_incr("connect_failed")
            await self.close(code=4400)
            return
        
        # Bind device atomically (thread-safe)
        try:
            # Synchronous call wrapped in sync_to_async (Channels does this automatically for DB)
            from channels.db import database_sync_to_async
            self.screen = await database_sync_to_async(bind_device_atomic)(
                token=token,
                device_id=self.device_id
            )
        except ScreenNotFoundError:
            logger.warning(f"WS connect rejected: token not found {token[:8]}...")
            ws_metrics.connection_failed()
            _ws_metric_incr("connect_failed")
            await self.close(code=4403)
            return
        except ScreenBoundError:
            logger.warning(
                f"WS connect rejected: screen {token[:8]}... bound to different device"
            )
            ws_metrics.connection_failed()
            _ws_metric_incr("connect_failed")
            await self.close(code=4408)
            return
        except Exception as e:
            logger.exception(f"WS connect error: {e}")
            ws_metrics.connection_failed()
            _ws_metric_incr("connect_failed")
            await self.close(code=4500)
            return
        
        # Server-derived groups (tenant isolation + per-screen targeting)
        self.school_group_name = school_group_name(self.screen.school_id)
        self.token_group_name = token_group_name(str(token), hash_len=16)

        # Per-school connection limit (SaaS protection)
        try:
            max_ws = int(getattr(settings, "WS_MAX_CONNECTIONS_PER_SCHOOL", 200) or 200)
            school_ws_key = f"ws:school_conns:{self.screen.school_id}"
            cache.add(school_ws_key, 0, timeout=86400)
            current = cache.incr(school_ws_key)
            if current > max_ws:
                cache.decr(school_ws_key)
                logger.warning(
                    "WS connect rejected: school %s exceeded max connections (%d/%d)",
                    self.screen.school_id, current, max_ws,
                )
                ws_metrics.connection_failed()
                _ws_metric_incr("connect_rejected_limit")
                await self.close(code=4429)
                return
        except Exception:
            pass  # fail-open: don't block connections if counter fails

        # Join school group (required). If this fails, the connection is unusable.
        try:
            await self.channel_layer.group_add(
                self.school_group_name,
                self.channel_name
            )
        except Exception as e:
            logger.exception(f"WS connect failed group_add school group: {e}")
            ws_metrics.connection_failed()
            _ws_metric_incr("connect_failed")
            await self.close(code=4501)
            return

        # Join token group (best-effort)
        if self.token_group_name:
            try:
                await self.channel_layer.group_add(self.token_group_name, self.channel_name)
            except Exception:
                self.token_group_name = None
        self._last_group_refresh_at = time.monotonic()
        
        # Accept connection
        await self.accept()
        await self._touch_presence()
        
        # Track successful connection
        connect_event = None
        _ws_metric_incr("connect_total")
        try:
            connect_info = ws_cluster_register_connect(
                channel_name=self.channel_name,
                token=self.token_value,
                device_id=self.device_id,
            )
            connect_event = str((connect_info or {}).get("event") or "")
        except Exception:
            pass
        is_reconnect = connect_event == "reconnect"
        ws_metrics.connection_opened(is_reconnect=is_reconnect)
        if is_reconnect:
            _ws_metric_incr("reconnect_total")
        
        if _should_log_ws_event("connect", screen_id=int(self.screen.id)):
            logger.info(
                f"WS connected: screen {self.screen.id} (school {self.screen.school_id}) "
                f"device {self.device_id[:8]}... group {self.school_group_name} event={connect_event or 'connect'}"
            )
        
        # Log metrics periodically (every 5 minutes)
        ws_metrics.log_if_needed(interval_seconds=300)
        self._start_server_ping_task()
    
    async def disconnect(self, close_code):
        """Leave school group on disconnect."""
        await self._stop_server_ping_task()

        # Decrement per-school connection counter
        if self.screen:
            try:
                school_ws_key = f"ws:school_conns:{self.screen.school_id}"
                val = cache.decr(school_ws_key)
                if val < 0:
                    cache.set(school_ws_key, 0, timeout=86400)
            except Exception:
                pass

        # Remember how this connection ended so the offline monitor can name a
        # cause instead of guessing (see core.screen_diagnostics).
        if self.screen and getattr(self.screen, "pk", None):
            record_disconnect_signal(
                int(self.screen.pk),
                source="ws_close",
                code=close_code,
            )

        # Track disconnection
        ws_metrics.connection_closed(close_code)
        _ws_metric_incr("disconnect_total")
        try:
            ws_cluster_register_disconnect(channel_name=self.channel_name, close_code=close_code)
        except Exception:
            pass
        if close_code is not None:
            try:
                _ws_metric_incr(f"disconnect_code:{int(close_code)}")
            except Exception:
                _ws_metric_incr(f"disconnect_code:{close_code}")
        
        if self.school_group_name:
            try:
                await self.channel_layer.group_discard(
                    self.school_group_name,
                    self.channel_name
                )
            except Exception:
                pass
            screen_id = self.screen.id if self.screen else None
            if _should_log_ws_event("disconnect", screen_id=screen_id, code=close_code):
                logger.info(
                    f"WS disconnected: screen {screen_id if screen_id is not None else '?'} "
                    f"code {close_code} group {self.school_group_name}"
                )

        if self.token_group_name:
            try:
                await self.channel_layer.group_discard(self.token_group_name, self.channel_name)
            except Exception:
                pass
    
    async def receive(self, text_data=None, bytes_data=None):
        """
        Handle client messages (ping/pong keepalive only).
        
        Client may send: {"type": "ping"}
        Server responds: {"type": "pong"}
        """
        if not text_data:
            return
        
        try:
            data = json.loads(text_data)
            msg_type = data.get("type")
            
            if msg_type == "ping":
                await self._touch_presence()
                try:
                    ws_cluster_heartbeat(channel_name=self.channel_name)
                except Exception:
                    pass
                await self.send(text_data=json.dumps({"type": "pong"}))
            elif msg_type == "pong":
                await self._touch_presence()
                # Accept client pong as heartbeat as well.
                try:
                    ws_cluster_heartbeat(channel_name=self.channel_name)
                except Exception:
                    pass
            else:
                logger.debug(f"WS received unknown message type: {msg_type}")
        except json.JSONDecodeError:
            logger.warning(f"WS received invalid JSON: {text_data[:100]}")
        except Exception as e:
            logger.exception(f"WS receive error: {e}")
    
    async def broadcast_invalidate(self, event):
        """
        Handle broadcast message from signals.
        
        Event structure (from channel_layer.group_send):
        {
            "type": "broadcast_invalidate",  # method name (snake_case)
            "revision": 123,
            "school_id": 5
        }
        
        Sends to client:
        {
            "type": "snapshot_refresh",
            "revision": 123
        }
        """
        revision = event.get("revision")
        school_id = event.get("school_id")
        target_screen_id = event.get("target_screen_id")
        
        # Sanity check: only send if school_id matches (defensive)
        if self.screen and school_id != self.screen.school_id:
            logger.warning(
                f"WS broadcast mismatch: screen school {self.screen.school_id} "
                f"got message for school {school_id}"
            )
            return
        if self.screen and target_screen_id and int(target_screen_id) != int(self.screen.id):
            return
        
        # Send snapshot refresh message to client
        start_time = time.time()
        try:
            await self.send(text_data=json.dumps({
                "type": "snapshot_refresh",
                "revision": revision,
                "reason": event.get("reason") or "content_changed",
            }))
            
            # Track successful broadcast
            latency_ms = (time.time() - start_time) * 1000
            ws_metrics.broadcast_sent(latency_ms)
            _ws_metric_incr("broadcast_sent")
            _ws_metric_incr("snapshot_refresh_total")

            screen_id = int(self.screen.id) if self.screen else 0
            if _should_log_ws_event("snapshot_refresh", screen_id=screen_id):
                logger.info(
                    f"WS sent snapshot_refresh: screen {self.screen.id if self.screen else '?'} "
                    f"school {school_id} revision {revision} latency {latency_ms:.1f}ms"
                )
        except Exception as e:
            ws_metrics.broadcast_failed()
            _ws_metric_incr("broadcast_failed")
            logger.exception(f"WS broadcast send failed: {e}")


    async def broadcast_reload(self, event):
        """Ask the client to reload the page (hard refresh behavior).

        This is used for per-screen maintenance or when a TV browser gets stuck.
        """
        school_id = event.get("school_id")
        target_screen_id = event.get("target_screen_id")
        if self.screen and school_id and int(school_id) != int(self.screen.school_id):
            return
        if self.screen and target_screen_id and int(target_screen_id) != int(self.screen.id):
            return

        start_time = time.time()
        try:
            await self.send(text_data=json.dumps({"type": "reload"}))
            latency_ms = (time.time() - start_time) * 1000
            ws_metrics.broadcast_sent(latency_ms)
            _ws_metric_incr("broadcast_sent")
        except Exception as e:
            ws_metrics.broadcast_failed()
            _ws_metric_incr("broadcast_failed")
            logger.exception(f"WS reload send failed: {e}")

    async def broadcast_patch(self, event):
        """Forward a small patch event to connected displays.

        Current dashboard changes mostly need a fresh cached snapshot, but keeping
        this handler gives the event flow a clear extension point for cheap UI
        patches without a full page reload.
        """
        school_id = event.get("school_id")
        if self.screen and school_id and int(school_id) != int(self.screen.school_id):
            return

        payload = event.get("patch")
        if not isinstance(payload, dict):
            payload = {}

        start_time = time.time()
        try:
            await self.send(text_data=json.dumps({
                "type": "patch",
                "revision": event.get("revision"),
                "patch": payload,
            }))
            latency_ms = (time.time() - start_time) * 1000
            ws_metrics.broadcast_sent(latency_ms)
            _ws_metric_incr("broadcast_sent")
            _ws_metric_incr("patch_total")
        except Exception as e:
            ws_metrics.broadcast_failed()
            _ws_metric_incr("broadcast_failed")
            logger.exception(f"WS patch send failed: {e}")

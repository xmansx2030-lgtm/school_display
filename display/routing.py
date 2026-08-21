"""
WebSocket URL routing for display screens.

All realtime invalidations are sent through:
    ws://domain/ws/display/
"""

from django.urls import path, re_path

from .consumers import DisplayConsumer, DisplayPreviewConsumer

websocket_urlpatterns = [
    path("ws/display/", DisplayConsumer.as_asgi()),
    re_path(r"^ws/display$", DisplayConsumer.as_asgi()),
    path("ws/display-preview/", DisplayPreviewConsumer.as_asgi()),
    re_path(r"^ws/display-preview$", DisplayPreviewConsumer.as_asgi()),
]

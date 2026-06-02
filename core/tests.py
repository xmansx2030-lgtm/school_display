from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from core.middleware import DisplayTokenMiddleware


class DisplayTokenMiddlewareTests(SimpleTestCase):
    def test_ws_metrics_path_does_not_require_display_token(self):
        request = RequestFactory().get("/api/display/ws-metrics/")
        middleware = DisplayTokenMiddleware(lambda req: JsonResponse({"ok": True}))

        response = middleware(request)

        self.assertEqual(response.status_code, 200)


class RootAssetTests(SimpleTestCase):
    def test_service_worker_is_served_at_root(self):
        for route_name in ("service_worker", "sw_js"):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/javascript; charset=utf-8")
                self.assertIn(b"self.addEventListener", response.content)

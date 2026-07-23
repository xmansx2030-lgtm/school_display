import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from core.middleware import DisplayTokenMiddleware, SecurityHeadersMiddleware
from core.display_presence import display_is_live, latest_display_presence, touch_display_presence
from core.models import DisplayScreen, School


class DisplayPresenceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة الحضور", slug="presence-school")
        self.screen = DisplayScreen.objects.create(
            name="الشاشة الرئيسية",
            school=self.school,
            is_active=True,
            bound_device_id="device-1",
        )

    def test_touch_updates_current_last_seen_field_and_live_state(self):
        seen_at = timezone.now()

        touch_display_presence(self.screen.pk, token=self.screen.token, seen_at=seen_at)

        self.screen.refresh_from_db()
        self.assertIsNotNone(self.screen.last_seen)
        self.assertAlmostEqual(self.screen.last_seen.timestamp(), seen_at.timestamp(), delta=1)
        self.assertAlmostEqual(latest_display_presence(self.screen).timestamp(), seen_at.timestamp(), delta=1)
        self.assertTrue(display_is_live(self.screen, now=seen_at))

    def test_cached_heartbeat_is_newer_than_throttled_database_value(self):
        first_seen = timezone.now()
        later_seen = first_seen + timedelta(seconds=10)
        touch_display_presence(self.screen.pk, token=self.screen.token, seen_at=first_seen)
        touch_display_presence(self.screen.pk, token=self.screen.token, seen_at=later_seen)

        self.screen.refresh_from_db()

        self.assertAlmostEqual(self.screen.last_seen.timestamp(), first_seen.timestamp(), delta=1)
        self.assertAlmostEqual(latest_display_presence(self.screen).timestamp(), later_seen.timestamp(), delta=1)


class DisplayTokenMiddlewareTests(SimpleTestCase):
    def test_ws_metrics_path_does_not_require_display_token(self):
        request = RequestFactory().get("/api/display/ws-metrics/")
        middleware = DisplayTokenMiddleware(lambda req: JsonResponse({"ok": True}))

        response = middleware(request)

        self.assertEqual(response.status_code, 200)


class RootAssetTests(SimpleTestCase):
    def test_platform_favicon_uses_the_requested_png_logo(self):
        expected = (
            Path(settings.BASE_DIR)
            / "static"
            / "img"
            / "school-display-logo-mark-white-preview (1).png"
        ).read_bytes()

        for route_name in ("favicon", "favicon_png"):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "image/png")
                self.assertEqual(response.content, expected)

    def test_service_worker_is_served_at_root(self):
        for route_name in ("service_worker", "sw_js"):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/javascript; charset=utf-8")
                self.assertIn(b"self.addEventListener", response.content)


class SecurityHeadersTests(SimpleTestCase):
    @override_settings(
        CONTENT_SECURITY_POLICY_ENABLED=True,
        CONTENT_SECURITY_POLICY_REPORT_ONLY=True,
        CONTENT_SECURITY_POLICY_REPORT_URI="/csp-report/",
    )
    def test_csp_starts_in_report_only_mode_with_restrictive_script_policy(self):
        request = RequestFactory().get("/")
        response = SecurityHeadersMiddleware(lambda _request: JsonResponse({"ok": True}))(request)

        policy = response["Content-Security-Policy-Report-Only"]
        self.assertIn("script-src 'self'", policy)
        self.assertNotIn("'unsafe-eval'", policy)
        self.assertIn("report-uri /csp-report/", policy)
        self.assertIn("camera=()", response["Permissions-Policy"])

    @override_settings(
        CONTENT_SECURITY_POLICY_ENABLED=True,
        CONTENT_SECURITY_POLICY_REPORT_ONLY=False,
    )
    def test_csp_can_be_switched_to_enforcement_mode(self):
        request = RequestFactory().get("/")
        response = SecurityHeadersMiddleware(lambda _request: JsonResponse({"ok": True}))(request)

        self.assertIn("Content-Security-Policy", response)
        self.assertNotIn("Content-Security-Policy-Report-Only", response)

    def test_csp_report_endpoint_accepts_browser_payload_without_reflecting_it(self):
        payload = {
            "csp-report": {
                "effective-directive": "script-src-elem",
                "blocked-uri": "https://evil.example/script.js?secret=value",
                "document-uri": "https://school-display.com/dashboard/?private=value",
            }
        }
        with self.assertLogs("core.csp", level="WARNING") as logs:
            response = self.client.post(
                reverse("csp_report"),
                data=json.dumps(payload),
                content_type="application/csp-report",
            )

        self.assertEqual(response.status_code, 204)
        self.assertNotIn("secret=value", " ".join(logs.output))
        self.assertNotIn("private=value", " ".join(logs.output))

    def test_primary_shells_no_longer_load_alpine_or_inline_shell_scripts(self):
        dashboard_shell = (Path(settings.BASE_DIR) / "templates" / "dashboard" / "_base.html").read_text(
            encoding="utf-8"
        )
        admin_shell = (Path(settings.BASE_DIR) / "templates" / "admin" / "admin_base.html").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("unpkg.com/alpinejs", dashboard_shell)
        self.assertNotIn("unpkg.com/alpinejs", admin_shell)
        self.assertIn("js/dashboard-shell.js", dashboard_shell)
        self.assertIn("js/admin-shell.js", admin_shell)

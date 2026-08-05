import json
import re
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.core.cache import cache
from django.core import mail
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from core.middleware import DisplayTokenMiddleware, SecurityHeadersMiddleware
from core.display_presence import display_is_live, latest_display_presence, touch_display_presence
from core.models import DisplayScreen, School, ScreenOutage, ScreenWeeklyUptimeReport, UserProfile
from core.screen_monitoring import scan_screens, send_weekly_uptime_reports
from schedule.models import SchoolSettings
from telegram_alerts.models import TelegramAlert
from django.contrib.auth import get_user_model


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


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="alerts@example.com",
    TELEGRAM_ALERTS_ENABLED=False,
)
class ScreenMonitoringTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة المراقبة", slug="monitoring-school")
        self.settings = SchoolSettings.objects.create(
            school=self.school,
            name=self.school.name,
            screen_offline_threshold_minutes=5,
            screen_offline_alerts_enabled=True,
            screen_offline_email_enabled=True,
            weekly_uptime_report_enabled=True,
        )
        self.manager = get_user_model().objects.create_user(
            username="monitor_manager",
            email="manager@example.com",
            password="StrongPass123!",
        )
        profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        profile.schools.add(self.school)
        self.inactive_manager = get_user_model().objects.create_user(
            username="inactive_monitor_manager",
            email="inactive-manager@example.com",
            password="StrongPass123!",
            is_active=False,
        )
        inactive_profile = UserProfile.objects.create(
            user=self.inactive_manager,
            active_school=self.school,
        )
        inactive_profile.schools.add(self.school)
        self.screen = DisplayScreen.objects.create(
            school=self.school,
            name="شاشة المدخل",
            is_active=True,
            bound_device_id="monitor-device",
            last_seen=timezone.now() - timedelta(minutes=20),
        )

    def test_offline_scan_alerts_once_then_resolves_after_reconnect(self):
        now = timezone.now()
        first = scan_screens(now=now)
        second = scan_screens(now=now + timedelta(minutes=1))

        self.assertEqual(first["opened"], 1)
        self.assertEqual(first["alerted"], 1)
        self.assertEqual(second["opened"], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["manager@example.com"])
        outage = ScreenOutage.objects.get(screen=self.screen)
        self.assertIsNotNone(outage.alert_sent_at)

        self.screen.last_seen = now + timedelta(minutes=2)
        self.screen.save(update_fields=("last_seen",))
        result = scan_screens(now=now + timedelta(minutes=2))
        self.assertEqual(result["resolved"], 1)
        outage.refresh_from_db()
        self.assertIsNotNone(outage.resolved_at)

    def test_newly_bound_screen_waits_for_the_configured_threshold(self):
        self.screen.last_seen = None
        self.screen.bound_at = timezone.now()
        self.screen.save(update_fields=("last_seen", "bound_at"))

        early = scan_screens(now=self.screen.bound_at + timedelta(minutes=4))
        late = scan_screens(now=self.screen.bound_at + timedelta(minutes=6))

        self.assertEqual(early["opened"], 0)
        self.assertEqual(early["alerted"], 0)
        self.assertEqual(late["opened"], 1)
        self.assertEqual(late["alerted"], 1)

    @override_settings(TELEGRAM_ALERTS_ENABLED=True)
    def test_weekly_report_is_one_admin_alert_and_sends_no_manager_email(self):
        week_start = timezone.localdate() - timedelta(days=14)
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(week_start, datetime.min.time()), tz)
        ScreenOutage.objects.create(
            screen=self.screen,
            detected_at=start + timedelta(days=1),
            resolved_at=start + timedelta(days=2),
        )
        second_school = School.objects.create(
            name="مدرسة النور",
            slug="alnoor-school",
        )
        SchoolSettings.objects.create(
            school=second_school,
            name=second_school.name,
            weekly_uptime_report_enabled=False,
        )
        second_screen = DisplayScreen.objects.create(
            school=second_school,
            name="شاشة الساحة",
            is_active=True,
            bound_device_id="second-monitor-device",
        )

        result = send_weekly_uptime_reports(week_start=week_start)
        repeated = send_weekly_uptime_reports(week_start=week_start)

        self.assertEqual(result["schools_sent"], 2)
        self.assertEqual(repeated["schools_sent"], 0)
        report = ScreenWeeklyUptimeReport.objects.get(screen=self.screen, week_start=week_start)
        self.assertEqual(report.offline_seconds, 24 * 60 * 60)
        self.assertAlmostEqual(float(report.uptime_percent), 85.71, places=2)
        self.assertIsNotNone(report.sent_at)
        self.assertIsNotNone(
            ScreenWeeklyUptimeReport.objects.get(
                screen=second_screen,
                week_start=week_start,
            ).sent_at
        )
        self.assertEqual(len(mail.outbox), 0)
        alert = TelegramAlert.objects.get(event_type="screen_uptime_weekly")
        self.assertIn(self.school.name, alert.message)
        self.assertIn(second_school.name, alert.message)
        self.assertIn(self.screen.name, alert.message)
        self.assertIn(second_screen.name, alert.message)

    @override_settings(TELEGRAM_ALERTS_ENABLED=True)
    def test_weekly_report_counts_offline_time_from_last_seen(self):
        week_start = timezone.localdate() - timedelta(days=21)
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(week_start, datetime.min.time()), tz)
        ScreenOutage.objects.create(
            screen=self.screen,
            last_seen_at=start + timedelta(hours=12),
            detected_at=start + timedelta(hours=12, minutes=5),
            resolved_at=start + timedelta(hours=13),
        )

        send_weekly_uptime_reports(week_start=week_start)

        report = ScreenWeeklyUptimeReport.objects.get(screen=self.screen, week_start=week_start)
        self.assertEqual(report.offline_seconds, 60 * 60)

    def test_weekly_report_waits_for_admin_telegram_without_emailing_manager(self):
        week_start = timezone.localdate() - timedelta(days=28)

        result = send_weekly_uptime_reports(week_start=week_start)

        report = ScreenWeeklyUptimeReport.objects.get(
            screen=self.screen,
            week_start=week_start,
        )
        self.assertEqual(result["schools_sent"], 0)
        self.assertIsNone(report.sent_at)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(TelegramAlert.objects.exists())


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
                # Match the release constant rather than a literal cache name, so
                # bumping the version does not require editing this assertion.
                self.assertRegex(
                    response.content.decode("utf-8"),
                    r"const RELEASE = '(v\d+)'",
                )

    def test_service_worker_respects_asset_versioning(self):
        """A `?v=` bump must reach the screen.

        The worker previously matched cached assets with `{ignoreSearch: true}`,
        so a new release resolved to the previously cached file and screens ran
        the old bundle indefinitely. Version-agnostic lookup is now allowed in
        exactly one place: the offline fallback inside `handleAsset`'s catch.
        """
        source = self.client.get(reverse("sw_js")).content.decode("utf-8")

        offline_branch = source.split("} catch (offline) {", 1)
        self.assertEqual(len(offline_branch), 2, "handleAsset lost its offline fallback branch")

        online_path = offline_branch[0]
        self.assertNotIn(
            "ignoreSearch",
            online_path,
            "the online asset path must key the cache on the full URL, query string included",
        )
        self.assertIn("ignoreSearch", offline_branch[1])

    def test_service_worker_precaches_the_bundle_the_page_actually_loads(self):
        """The shell list must track what display.html links.

        It used to precache `display.js` while the page loaded `display.min.js`,
        so every screen downloaded 296 KB it never executed and the bundle it did
        execute was never available offline.
        """
        source = self.client.get(reverse("sw_js")).content.decode("utf-8")
        shell = source.split("const SHELL_ASSETS = [", 1)[1].split("]", 1)[0]
        display_template = (
            Path(settings.BASE_DIR) / "templates" / "website" / "display.html"
        ).read_text(encoding="utf-8")

        for asset in re.findall(r"'(/static/[^']+)'", shell):
            with self.subTest(asset=asset):
                self.assertIn(
                    asset.removeprefix("/static/"),
                    display_template,
                    f"{asset} is precached but no longer linked by the display page",
                )

    def test_display_shell_contains_scheduled_occasion_theme_runtime(self):
        display_template = (
            Path(settings.BASE_DIR) / "templates" / "website" / "display.html"
        ).read_text(encoding="utf-8")
        display_script = (
            Path(settings.BASE_DIR) / "static" / "js" / "display.js"
        ).read_text(encoding="utf-8")

        self.assertIn('id="occasionThemeDecor"', display_template)
        self.assertIn('id="occasionHero"', display_template)
        self.assertIn('id="occasionHeroTitle"', display_template)
        self.assertIn("occasion-hero__mark", display_template)
        self.assertIn("function applyOccasionTheme", display_script)
        self.assertIn("function refreshOccasionPresentation", display_script)
        self.assertIn('removeAttribute("data-occasion-theme")', display_script)
        self.assertIn('setAttribute("data-occasion-ambient", "1")', display_script)
        self.assertIn("const offsetX", display_script)
        self.assertIn("const offsetY", display_script)

    def test_display_shell_renders_a_theme_for_every_registered_occasion(self):
        """الهوية البصرية تُولَّد من السجل، فلا تُكتب أي مناسبة يدويًا هنا.

        نصيّر الصفحة فعلًا بدل تفتيش نصّها: هذا يكشف مناسبة أُضيفت للسجل ولم
        يصلها لون، وهو بالضبط الخلل الصامت الذي كان التكرار يسبّبه.
        """
        from core import occasions

        html = render_to_string(
            "website/display.html",
            {
                "occasion_themes": occasions.all_occasions(),
                "occasion_theme_meta_json": json.dumps(
                    occasions.theme_map(), ensure_ascii=False
                ),
            },
        )

        for occasion in occasions.all_occasions():
            with self.subTest(occasion=occasion.key):
                self.assertIn(f'body[data-occasion-theme="{occasion.key}"]', html)
                self.assertIn(occasion.accent, html)
                self.assertIn(occasion.deep, html)

        # الثيمات المتقاعدة يجب ألا تُصيَّر إطلاقًا.
        for retired in occasions.RETIRED_OCCASION_KEYS:
            self.assertNotIn(f'body[data-occasion-theme="{retired}"]', html)

    def test_display_shell_publishes_occasion_meta_for_the_client(self):
        """``display.js`` يقرأ بيانات المناسبات من الصفحة لا من نسخة خاصة به."""
        from core import occasions

        html = render_to_string(
            "website/display.html",
            {
                "occasion_themes": occasions.all_occasions(),
                "occasion_theme_meta_json": json.dumps(
                    occasions.theme_map(), ensure_ascii=False
                ),
            },
        )

        match = re.search(
            r'<script type="application/json" id="occasionThemeMeta">(.*?)</script>',
            html,
            re.S,
        )
        self.assertIsNotNone(match, "كتلة بيانات المناسبات مفقودة من الصفحة")

        published = json.loads(match.group(1))
        self.assertEqual(set(published), set(occasions.OCCASIONS))
        for key, meta in published.items():
            with self.subTest(occasion=key):
                self.assertTrue(meta["label"])
                self.assertTrue(meta["mark"])
                self.assertTrue(meta["badgeIcon"])
                self.assertEqual(len(meta["symbols"]), 2)


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
        self.assertIn("https://static.cloudflareinsights.com", policy)
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

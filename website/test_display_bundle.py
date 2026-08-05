"""The display page must serve the bundle the settings ask for."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import DisplayScreen, School, SubscriptionPlan
from schedule.models import SchoolSettings
from subscriptions.models import SchoolSubscription


class DisplayBundleSelectionTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة الحزمة", slug="bundle-school")
        SchoolSettings.objects.create(school=self.school, name=self.school.name)
        self.screen = DisplayScreen.objects.create(school=self.school, name="شاشة", is_active=True)
        plan = SubscriptionPlan.objects.create(
            code="bundle-plan",
            name="خطة",
            price=Decimal("100.00"),
            duration_days=365,
            max_screens=3,
        )
        SchoolSubscription.objects.create(
            school=self.school,
            plan=plan,
            starts_at=timezone.localdate(),
            status="active",
        )

    def _page(self):
        return self.client.get(reverse("website:short_display", args=[self.screen.short_code]))

    @override_settings(DISPLAY_USE_MINIFIED_JS=True)
    def test_minified_bundle_is_served_by_default(self):
        response = self._page()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js/display.min.js")

    @override_settings(DISPLAY_USE_MINIFIED_JS=False)
    def test_readable_bundle_can_be_restored_for_debugging(self):
        response = self._page()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "js/display.js")
        self.assertNotContains(response, "js/display.min.js")

    @override_settings(DISPLAY_USE_MINIFIED_JS=True)
    def test_service_worker_registration_is_external(self):
        """Inline registration would be blocked by the enforced CSP."""
        response = self._page()

        self.assertContains(response, "js/display-sw-register.js")


class DisplayPageWeightTests(DisplayBundleSelectionTests):
    """The board's first paint must not wait on anything it does not control."""

    def test_no_render_blocking_third_party_stylesheet(self):
        """A filtered or slow school network must not delay the first frame.

        The page used to pull IBM Plex Sans Arabic from fonts.googleapis.com as a
        render-blocking cross-origin stylesheet, which the Service Worker cannot
        cache either. The faces are served from our own origin now.
        """
        html = self._page().content.decode("utf-8")

        self.assertNotIn("fonts.googleapis.com/css", html)
        self.assertNotIn("fonts.gstatic.com", html)
        self.assertIn("css/fonts.css", html)

    def test_board_styles_are_a_cacheable_file(self):
        """~100 KB of inline <style> was re-sent and re-parsed on every load.

        Only the per-school occasion palettes generated from core/occasions.py
        may stay inline, and they have to come after the stylesheet so they keep
        overriding it.
        """
        html = self._page().content.decode("utf-8")

        self.assertIn("css/display-board.css", html)

        inline_css = "".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S))
        self.assertLess(
            len(inline_css),
            12_000,
            "inline CSS is creeping back into the display page; it belongs in display-board.css",
        )
        self.assertIn("--occasion-accent", inline_css)
        self.assertLess(
            html.index("css/display-board.css"),
            html.index("--occasion-accent"),
            "occasion palettes must be emitted after display-board.css to win the cascade",
        )

    def test_lite_mode_is_resolved_server_side_for_televisions(self):
        """A TV must never paint the heavy design, not even for one frame.

        display.js can detect the device itself, but only after a deferred
        bundle parses — long after the browser has painted the blurred orbs and
        glass panels once.
        """
        tv = self.client.get(
            reverse("website:short_display", args=[self.screen.short_code]),
            HTTP_USER_AGENT="Mozilla/5.0 (SMART-TV; Linux; Tizen 5.5) AppleWebKit/537.36",
        )
        self.assertIn('data-lite="1"', tv.content.decode("utf-8"))

    def test_lite_mode_is_left_to_the_client_on_unknown_devices(self):
        """An empty value hands the decision to the client's hardware heuristics."""
        desktop = self.client.get(
            reverse("website:short_display", args=[self.screen.short_code]),
            HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36",
        )
        self.assertIn('data-lite=""', desktop.content.decode("utf-8"))

    def test_lite_mode_query_override_wins_in_both_directions(self):
        url = reverse("website:short_display", args=[self.screen.short_code])
        tv_ua = "Mozilla/5.0 (SMART-TV; Linux; Tizen 5.5) AppleWebKit/537.36"
        pc_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"

        forced_on = self.client.get(f"{url}?lite=1", HTTP_USER_AGENT=pc_ua)
        self.assertIn('data-lite="1"', forced_on.content.decode("utf-8"))

        forced_off = self.client.get(f"{url}?lite=0", HTTP_USER_AGENT=tv_ua)
        self.assertIn('data-lite="0"', forced_off.content.decode("utf-8"))

    def test_lite_mode_never_enters_the_shared_context_cache(self):
        """The context dict is cached per token+revision; lite mode is per device."""
        url = reverse("website:short_display", args=[self.screen.short_code])
        tv_ua = "Mozilla/5.0 (SMART-TV; Linux; Tizen 5.5) AppleWebKit/537.36"
        pc_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"

        # Warm the cache from the TV, then make sure a desktop is not served its answer.
        self.assertIn('data-lite="1"', self.client.get(url, HTTP_USER_AGENT=tv_ua).content.decode("utf-8"))
        self.assertIn('data-lite=""', self.client.get(url, HTTP_USER_AGENT=pc_ua).content.decode("utf-8"))
        self.assertIn('data-lite="1"', self.client.get(url, HTTP_USER_AGENT=tv_ua).content.decode("utf-8"))


class DisplayBoardStylesheetTests(SimpleTestCase):
    """Guards on the extracted stylesheet itself."""

    def setUp(self):
        self.css = (
            Path(settings.BASE_DIR) / "static" / "css" / "display-board.css"
        ).read_text(encoding="utf-8")

    def test_lite_mode_covers_the_expensive_board_effects(self):
        """These four were only ever disabled by `prefers-reduced-motion`.

        No television reports that preference, so the weakest devices in the
        fleet kept paying for the most expensive effects on the page.
        """
        for selector in (
            'body[data-lite="1"] .hero-card__orb',
            'body[data-lite="1"] .hero-card__ambient',
            'body[data-lite="1"] .header-live-status__pulse',
            'body[data-lite="1"] .countdown-idle-glyph',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, self.css)

    def test_stage_avoids_permanent_layer_promotion_and_forced_kerning(self):
        """#fitStage is a 1920×1080 canvas holding every backdrop-filter panel.

        `will-change: transform` pinned that as a GPU texture for the whole
        session even though the fit scale only changes on a viewport resize, and
        `optimizeLegibility` forced kerning for every glyph on every layout pass.
        """
        stage = self.css.split("#fitStage {", 1)[1].split("}", 1)[0]
        declarations = re.sub(r"/\*.*?\*/", "", stage, flags=re.S)

        self.assertNotIn("will-change", declarations)
        self.assertNotIn("optimizeLegibility", declarations)
        # The scrollers still animate every frame, so they keep their promotion.
        self.assertIn(".track {", self.css)

    def test_no_django_template_syntax_leaked_into_the_static_file(self):
        """The occasion loops must have stayed behind in the template."""
        self.assertNotIn("{%", self.css)
        self.assertNotIn("{{", self.css)

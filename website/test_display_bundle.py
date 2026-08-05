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

    def test_board_tokens_are_scoped_where_the_theme_variables_live(self):
        """Derived board tokens must not be computed on :root.

        app.css declares --accent-main/--accent-sub/--mesh1/--mesh2 on
        `body.display-board`. A `:root` block reading them resolves to the indigo
        fallback instead — and then inherits that down to the whole board, so the
        2026 layer stayed indigo whatever theme the school picked.
        """
        stripped = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        theme_vars = r"--(?:accent-main|accent-sub|mesh1|mesh2|glass-surface|glass-border|bg-deep)"

        offenders = []
        for match in re.finditer(r"(^|\n)([ \t]*):root\s*\{(.*?)\n\2\}", stripped, re.S):
            if re.search(rf"var\(\s*{theme_vars}", match.group(3)):
                offenders.append(stripped[: match.start()].count("\n") + 2)

        self.assertEqual(
            offenders,
            [],
            f":root blocks at lines {offenders} read variables declared on body.display-board",
        )

    def test_board_tokens_are_declared_and_balanced(self):
        self.assertIn("--board-tint-1", self.css)
        self.assertIn("--board-accent-border", self.css)
        stripped = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        self.assertEqual(stripped.count("{"), stripped.count("}"))

    def test_every_palette_class_in_the_markup_is_themed_or_semantic(self):
        """A theme choice must reach the whole board.

        Fixed Tailwind palette classes are allowed only where the colour carries
        meaning — the emergency banner has to read as an alarm no matter what
        palette the school picked.
        """
        markup = (
            Path(settings.BASE_DIR) / "templates" / "website" / "display.html"
        ).read_text(encoding="utf-8").split("<body", 1)[1]
        tailwind = (
            Path(settings.BASE_DIR) / "static" / "css" / "tailwind.generated.css"
        ).read_text(encoding="utf-8")

        palette = r"(?:emerald|orange|amber|purple|red|rose|cyan|sky|violet|teal|lime|yellow|green|blue)"
        exempt = {
            # Emergency banner: red has to read as an alarm, whatever the palette.
            "bg-red-500", "bg-red-500/10", "text-red-200",
            # Already neutralised by `aside .glass-panel > div:first-child`, which
            # gives both side-panel headers the same white wash at a higher
            # specificity. An override keyed on the utility could never win.
            "bg-orange-500/5",
        }

        def defined_in(sheet, utility):
            """Exact class match — `bg-orange-500/5` must not match `/50`."""
            escaped = re.escape(utility.replace("/", r"\/"))
            return re.search(rf"\.{escaped}(?![\w\\/-])", sheet) is not None

        unthemed = []
        for utility in sorted(set(re.findall(
            rf"\b(?:bg|text|border|from|to|via)-{palette}-\d{{2,3}}(?:/\d{{1,3}})?", markup
        ))):
            if utility in exempt:
                continue
            # A class Tailwind never generated paints nothing, so it cannot
            # override the theme either.
            if not defined_in(tailwind, utility):
                continue
            if not defined_in(self.css, utility):
                unthemed.append(utility)

        self.assertEqual(
            unthemed,
            [],
            f"these classes render a fixed colour the theme cannot reach: {unthemed}",
        )


class DisplayEndOfDayPanelTests(SimpleTestCase):
    """Day-scoped panels must switch off once the school day is over.

    Verified end to end in headless Chrome against a stubbed `after` snapshot:
    with the previous bundle the honour board stayed VISIBLE and featuredEmpty
    was "0"; it now reports HIDDEN and "1", alongside the standby card, the
    period-classes card and the whole side column.

    These assertions pin the source-level invariants behind that result, in the
    same style as the other display.js guards in this suite — the project has no
    JavaScript test runner, so the browser check is a manual step and these keep
    the wiring from silently regressing.
    """

    def setUp(self):
        self.js = (
            Path(settings.BASE_DIR) / "static" / "js" / "display.js"
        ).read_text(encoding="utf-8")

    def _body(self, name):
        """Return the source of a top-level `function name(...) { ... }`."""
        start = self.js.index(f"function {name}(")
        depth, i = 0, self.js.index("{", start)
        for j in range(i, len(self.js)):
            if self.js[j] == "{":
                depth += 1
            elif self.js[j] == "}":
                depth -= 1
                if depth == 0:
                    return self.js[start : j + 1]
        raise AssertionError(f"could not delimit {name}")

    def test_featured_panel_is_gated_on_day_over(self):
        """The honour and duty boards were the only panels with no day-over gate."""
        body = self._body("renderFeaturedPanel")

        self.assertIn("rt.dayOver", body)
        for flag in ("screenShowDuty", "screenShowExcellence"):
            with self.subTest(flag=flag):
                self.assertRegex(
                    body,
                    rf"!dayOver && screenFlag\(\"{flag}\"",
                    f"{flag} must be combined with the day-over gate",
                )

    def test_featured_panel_renders_the_gated_lists(self):
        """A hidden honour board must not keep its rotation interval running."""
        body = self._body("renderFeaturedPanel")

        self.assertIn("renderDuty({ items: dutyRaw })", body)
        self.assertIn("renderExcellence(excellenceRaw)", body)

    def test_standby_and_period_lists_clear_when_the_day_is_over(self):
        for name in ("renderStandby", "renderPeriodClasses"):
            with self.subTest(function=name):
                self.assertIn("if (rt.dayOver) arr = [];", self._body(name))

    def test_day_over_transition_clears_every_day_scoped_panel(self):
        """The local clock, not the next poll, must clear the board.

        dayEngineApplyDayOver fires the moment the last block ends. Without
        these calls the board kept showing standby rows and the honour board
        until the next payload — up to a full out-of-hours poll interval.
        """
        body = self._body("dayEngineApplyDayOver")

        for call in ("renderPeriodClasses([])", "renderStandby([])", "renderFeaturedPanel("):
            with self.subTest(call=call):
                self.assertIn(call, body)

    def test_day_over_excludes_states_where_the_day_has_not_finished(self):
        """`holiday` and `before_hours` are not "the day ended"."""
        body = self._body("computeDayOver")

        self.assertIn('if (stType === "holiday" || stateReason === "holiday" || stateReason === "before_hours") return false;', body)
        self.assertIn('if (stType === "off" || stType === "after" || stateReason === "after_hours") return true;', body)

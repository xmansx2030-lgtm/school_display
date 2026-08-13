"""Browser checks for the manager dashboard's layout behaviour.

Three of the defects found while unifying the dashboard were invisible to a
render test, because the markup was correct and only the *computed layout* was
wrong:

* the sticky save bar never actually stuck — it tracked the page 1:1, because
  its containing block was exactly its own height and `.dashboard-main` was a
  scroll container that never scrolls;
* on a phone the live-preview panel overlapped the fields instead of stacking
  under them, which does not widen the page and so passes an overflow check;
* a form's script silently did nothing.

Those need a real engine, so this drives the pages in headless Chromium.

The suite is skipped — not failed — when Playwright or its browser is missing,
so a plain `manage.py test` on a fresh clone still runs clean. To enable it::

    pip install -r requirements-dev.txt
    python -m playwright install chromium
"""

from __future__ import annotations

import asyncio
import unittest

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone

from core.models import DisplayScreen, School, SubscriptionPlan, UserProfile
from schedule.models import SchoolSettings
from subscriptions.models import SchoolSubscription

try:  # pragma: no cover - import guard, not behaviour
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


PASSWORD = "StrongPass123!"


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class ManagerLayoutBrowserTests(StaticLiveServerTestCase):
    """Layout contracts that only a rendering engine can confirm."""

    def setUp(self):
        # Order matters. Playwright's sync API drives its event loop from a
        # greenlet on this thread, and while it is alive Django sees a running
        # loop and refuses every ORM call as `SynchronousOnlyOperation`. So all
        # the database work happens first, and the browser is started only
        # afterwards — and stopped in tearDown, before Django flushes.
        school = School.objects.create(name="مدرسة المعاينة", slug="browser-school")
        user = get_user_model().objects.create_user(username="browser_manager", password=PASSWORD)
        profile = UserProfile.objects.create(user=user, active_school=school)
        profile.schools.add(school)
        SchoolSettings.objects.create(name=school.name, school=school)
        plan = SubscriptionPlan.objects.create(
            code="browser-plan",
            name="خطة",
            price=1,
            duration_days=365,
            max_screens=3,
            max_users=3,
        )
        SchoolSubscription.objects.create(
            school=school, plan=plan, starts_at=timezone.localdate(), status="active"
        )
        DisplayScreen.objects.create(school=school, name="شاشة البهو")

        # Django's import chain leaves Windows on the selector event loop, and
        # that one cannot spawn the browser subprocess.
        if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch()
        except Exception as exc:  # the engine was never downloaded
            self.playwright.stop()
            raise unittest.SkipTest(
                f"chromium unavailable — run `python -m playwright install chromium` ({exc})"
            )

        self.context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        self.page = self.context.new_page()
        self.js_errors: list[str] = []
        self.page.on("pageerror", lambda e: self.js_errors.append(str(e)))

        self.page.goto(f"{self.live_server_url}/dashboard/login/")
        self.page.fill('input[name="username"]', "browser_manager")
        self.page.fill('input[name="password"]', PASSWORD)
        self.page.click('button[type="submit"]')
        self.page.wait_for_load_state("networkidle")

    def tearDown(self):
        # Must happen here rather than in `_post_teardown`: Django flushes the
        # database after this, and that is an ORM call the running loop would
        # reject.
        self.context.close()
        self.browser.close()
        self.playwright.stop()

    def open(self, path, *, width=1440, height=900):
        self.page.set_viewport_size({"width": width, "height": height})
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.wait_for_load_state("networkidle")

    # ------------------------------------------------------------------ sticky

    def test_the_save_bar_stays_on_screen_while_the_form_is_scrolled(self):
        self.open("/dashboard/announcements/new/")
        bar = self.page.locator(".dp-actionbar")

        scrolled = 400
        positions = []
        for offset in (0, scrolled // 2, scrolled):
            self.page.evaluate(f"window.scrollTo(0, {offset})")
            self.page.wait_for_timeout(180)
            box = bar.bounding_box()
            positions.append(round(box["y"] + box["height"]))

        viewport = self.page.evaluate("window.innerHeight")

        # It may drift a little at the end — a bottom-sticky box releases once
        # its own resting place scrolls into range, and that is correct. What
        # must never happen is it tracking the page one-for-one, which is how
        # it behaved while it sat in a wrapper its own height.
        for position in positions:
            self.assertLessEqual(
                position, viewport, f"the save bar left the viewport: {positions}"
            )
        drift = max(positions) - min(positions)
        self.assertLess(
            drift,
            scrolled // 4,
            f"the save bar moved with the page instead of pinning: {positions}",
        )

    def test_the_preview_panel_stays_on_screen_while_the_form_is_scrolled(self):
        self.open("/dashboard/announcements/new/")
        aside = self.page.locator(".dp-form__aside")

        self.page.evaluate("window.scrollTo(0, 400)")
        self.page.wait_for_timeout(180)
        top = aside.bounding_box()["y"]

        # It pins just under the fixed header rather than scrolling off.
        self.assertGreater(top, 0, f"the preview panel scrolled off the top ({top})")
        self.assertLess(top, 200, f"the preview panel is not pinned ({top})")

    # ------------------------------------------------------------------ mobile

    def test_the_preview_panel_stacks_under_the_fields_on_a_phone(self):
        for path in (
            "/dashboard/announcements/new/",
            "/dashboard/excellence/new/",
            "/dashboard/duty/new/",
            "/dashboard/standby/new/",
        ):
            with self.subTest(page=path):
                self.open(path, width=390, height=844)
                geometry = self.page.evaluate(
                    """() => {
                      const main = document.querySelector('.dp-form__main');
                      const aside = document.querySelector('.dp-form__aside');
                      const m = main.getBoundingClientRect();
                      const a = aside.getBoundingClientRect();
                      return {stacked: a.top >= m.bottom - 2, mainWidth: Math.round(m.width)};
                    }"""
                )
                self.assertTrue(
                    geometry["stacked"],
                    "the preview panel sits beside the fields on a phone and covers them",
                )
                self.assertGreater(
                    geometry["mainWidth"],
                    300,
                    f"the field column was squeezed to {geometry['mainWidth']}px",
                )

    def test_no_dashboard_page_scrolls_sideways(self):
        pages = [
            "/dashboard/announcements/", "/dashboard/announcements/new/",
            "/dashboard/excellence/", "/dashboard/excellence/new/",
            "/dashboard/lessons/", "/dashboard/support/", "/dashboard/support/new/",
            "/dashboard/schools/add/", "/dashboard/school-data/import/",
            "/dashboard/screens/new/", "/dashboard/emergency-alerts/",
        ]
        for path in pages:
            for width in (1440, 390):
                with self.subTest(page=path, width=width):
                    self.open(path, width=width, height=900)
                    overflow = self.page.evaluate(
                        "() => document.documentElement.scrollWidth"
                        " - document.documentElement.clientWidth"
                    )
                    self.assertEqual(overflow, 0, f"{path} scrolls {overflow}px sideways")

    # -------------------------------------------------------------- behaviour

    def test_the_announcement_form_reacts_to_input(self):
        self.open("/dashboard/announcements/new/")

        targets = self.page.locator("#announcementScreenTargets")
        self.assertTrue(targets.is_hidden(), "the screen picker shows before it is relevant")
        self.page.select_option("#id_scope", "screens")
        self.page.wait_for_timeout(120)
        self.assertTrue(targets.is_visible(), "picking 'screens' did not reveal the screen picker")

        self.page.fill("#id_title", "اختبار المعاينة")
        self.page.wait_for_timeout(120)
        self.assertEqual(self.page.inner_text("#previewTitle"), "اختبار المعاينة")

        self.assertEqual(self.js_errors, [])

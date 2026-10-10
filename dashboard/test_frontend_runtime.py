"""Browser regressions for dashboard interaction and slow-network saves.

Small DOM fixtures keep network timing deterministic; the manager browser
suite separately exercises the rendered Django pages and responsive layout.
"""
import asyncio
import re
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "playwright is not installed")
class DashboardInteractionTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:
            cls.playwright.stop()
            raise unittest.SkipTest(f"chromium unavailable: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 390, "height": 844})
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.page.close()
        self.assertEqual(self.errors, [])

    def script(self, name):
        self.page.add_script_tag(path=str(Path(settings.BASE_DIR) / "static/js" / name))

    def editor(self):
        self.page.set_content('''<form data-tt-form data-tt-autosave="true">
          <div data-tt-row>
            <select name="subject-1-1" data-tt-subject>
              <option value=""></option><option selected value="math">Math</option>
              <option value="science">Science</option><option value="arabic">Arabic</option>
            </select>
            <select name="teacher-1-1" data-tt-teacher>
              <option value=""></option><option selected value="ali">Ali</option>
            </select>
          </div>
          <span data-tt-state></span><span data-tt-summary></span>
          <button type="button" data-tt-reset>Undo</button>
          <button type="submit" id="save">Save</button>
        </form><div data-tt-autosave-toast></div>''')
        self.page.evaluate('''() => {
          window.requests = []; window.submissions = [];
          window.fetch = (url, options) => new Promise((resolve, reject) => {
            const request = {values: Object.fromEntries(options.body), resolve, reject};
            requests.push(request);
            options.signal?.addEventListener('abort', () => {
              request.aborted = true;
              reject(new DOMException('Aborted', 'AbortError'));
            });
          });
          window.finishSave = index => requests[index].resolve({
            ok: true, json: () => Promise.resolve({ok: true})
          });
        }''')
        self.script("timetable-day.js")
        self.page.evaluate('''() => document.querySelector('form').addEventListener('submit', e => {
          if (!e.defaultPrevented) submissions.push(Object.fromEntries(new FormData(e.target)));
          e.preventDefault();
        })''')

    def change_subject(self, value):
        self.page.select_option("[data-tt-subject]", value)

    def wait_for_saves(self, count):
        self.page.wait_for_function("count => requests.length === count", arg=count, timeout=4000)

    def test_autosaves_are_serialized_and_keep_the_latest_edit(self):
        self.editor()
        self.change_subject("science")
        self.wait_for_saves(1)
        self.change_subject("arabic")
        self.page.wait_for_timeout(1050)
        self.assertEqual(self.page.evaluate("requests.length"), 1)
        self.page.evaluate("finishSave(0)")
        self.wait_for_saves(2)
        self.assertEqual(self.page.evaluate("requests[1].values['subject-1-1']"), "arabic")
        self.page.evaluate("finishSave(1)")
        self.page.wait_for_function("document.querySelector('[data-tt-reset]').disabled")

    def test_undo_during_save_restores_the_server_as_well_as_the_form(self):
        self.editor()
        self.change_subject("science")
        self.wait_for_saves(1)
        self.page.click("[data-tt-reset]")
        self.page.evaluate("finishSave(0)")
        self.wait_for_saves(2)
        self.assertEqual(self.page.evaluate("requests[1].values['subject-1-1']"), "math")
        self.page.evaluate("finishSave(1)")
        self.page.wait_for_function("document.querySelector('[data-tt-reset]').disabled")

    def test_leaving_during_an_autosave_still_warns_about_unsaved_edits(self):
        self.editor()
        self.change_subject("science")
        self.wait_for_saves(1)
        guarded = self.page.evaluate('''() => {
          const event = new Event('beforeunload', {cancelable: true});
          dispatchEvent(event); return event.defaultPrevented;
        }''')
        self.assertTrue(guarded)

    def test_manual_save_waits_for_the_running_autosave(self):
        self.editor()
        self.change_subject("science")
        self.wait_for_saves(1)
        self.change_subject("arabic")
        self.page.click("#save")
        self.assertEqual(self.page.evaluate("submissions.length"), 0)
        self.assertFalse(self.page.evaluate("Boolean(requests[0].aborted)"))
        self.page.evaluate("finishSave(0)")
        self.page.wait_for_function("submissions.length === 1")
        self.assertEqual(self.page.evaluate("submissions[0]['subject-1-1']"), "arabic")

    def test_server_validation_message_preserves_unsaved_edits_as_plain_text(self):
        self.editor()
        self.change_subject("science")
        self.wait_for_saves(1)
        self.page.evaluate('''requests[0].resolve({ok: false, json: () => Promise.resolve({
          ok: false, message: 'المعلم غير متاح <img src=x onerror=alert(1)>'
        })})''')
        self.page.wait_for_function("document.querySelector('[data-tt-autosave-toast]').classList.contains('is-error')")
        self.assertIn("المعلم غير متاح <img", self.page.locator("[data-tt-autosave-toast]").inner_text())
        self.assertEqual(self.page.locator("[data-tt-autosave-toast] img").count(), 0)
        self.assertFalse(self.page.locator("[data-tt-reset]").is_disabled())
        self.page.click("[data-tt-reset]")
        self.assertEqual(self.page.locator("[data-tt-subject]").input_value(), "math")

    def test_incomplete_period_blocks_manual_save_and_focuses_the_missing_field(self):
        self.editor()
        self.page.select_option("[data-tt-teacher]", "")
        self.page.click("#save")
        self.assertEqual(self.page.evaluate("submissions.length"), 0)
        self.assertEqual(self.page.evaluate("requests.length"), 0)
        self.assertTrue(self.page.locator("[data-tt-teacher]").evaluate("e => e === document.activeElement"))
        self.assertIn("أكمل المادة والمعلم", self.page.locator("[data-tt-autosave-toast]").inner_text())

    def test_failed_native_validation_does_not_authorize_a_later_submission(self):
        self.page.set_content('''<form>
          <input name="title" required aria-label="Title">
          <button type="submit" data-confirm="Confirm send">Send</button>
        </form>''')
        self.script("csp-actions.js")
        self.page.evaluate('''() => {
          window.submitted = 0;
          document.querySelector('form').addEventListener('submit', e => {
            if (!e.defaultPrevented) submitted++;
            e.preventDefault();
          });
        }''')
        self.page.click("button[data-confirm]")
        self.page.click("[data-confirm-submit]")
        self.page.fill("input", "A later valid action")
        self.page.evaluate("document.querySelector('form').requestSubmit()")
        self.assertTrue(self.page.locator("dialog").evaluate("e => e.open"))
        self.assertEqual(self.page.evaluate("submitted"), 0)

    def test_mobile_sidebar_manages_focus_and_restores_desktop_navigation(self):
        self.page.set_content('''<button id="dashboardSidebarToggleBtn">Menu</button>
          <div id="dashboardMobileOverlay"></div>
          <aside id="dashboardSidebar"><a href="#first">First</a><a href="#last">Last</a></aside>
          <main class="dashboard-main"><button id="background">Background</button></main>''')
        self.script("dashboard-shell.js")
        self.page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
        self.page.locator("#dashboardSidebar a").first.focus()
        self.assertFalse(self.page.evaluate("document.querySelector('#dashboardSidebar').contains(document.activeElement)"))
        self.page.click("#dashboardSidebarToggleBtn")
        self.assertTrue(self.page.evaluate("document.querySelector('#dashboardSidebar').contains(document.activeElement)"))
        self.page.keyboard.press("Escape")
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "dashboardSidebarToggleBtn")
        self.page.set_viewport_size({"width": 1440, "height": 900})
        self.page.wait_for_function("!document.querySelector('#dashboardSidebar').inert")
        self.page.locator("#dashboardSidebar a").first.focus()
        self.assertTrue(self.page.evaluate("document.querySelector('#dashboardSidebar').contains(document.activeElement)"))

    def test_shell_keeps_fixed_surfaces_out_of_normal_document_flow(self):
        self.page.set_content('''<body class="dashboard-shell">
          <style>.dashboard-topbar,.mobile-overlay,.dashboard-toast-stack{position:fixed;inset:0;}</style>
          <header class="dashboard-topbar"></header><div class="mobile-overlay"></div>
          <div class="dashboard-toast-stack"></div><div class="dashboard-layout-shell"></div>
        </body>''')
        self.page.add_style_tag(path=str(Path(settings.BASE_DIR) / "static/css/dashboard-luxury.css"))
        positions = self.page.locator("header,.mobile-overlay,.dashboard-toast-stack").evaluate_all(
            "elements => elements.map(e => getComputedStyle(e).position)"
        )
        self.assertEqual(positions, ["fixed"] * 3)

    def test_settings_tabs_recover_unknown_hashes_and_reveal_server_errors(self):
        template = (Path(settings.BASE_DIR) / "templates/dashboard/settings.html").read_text(encoding="utf-8")
        script = re.search(r"<script[^>]*>(.*?)</script>", template, re.S).group(1)
        for fragment, error, expected in (("unknown", "", "runtime"), ("runtime", '<div class="settings-field-error">Invalid email</div>', "contact")):
            with self.subTest(fragment=fragment, error=bool(error)):
                self.page.goto("about:blank")
                self.page.set_content(f'''<style>.hidden{{display:none}}</style>
                  <div class="dashboard-settings" data-initial-settings-tab="runtime">
                    <button data-settings-tab="runtime">Runtime</button>
                    <button data-settings-tab="contact">Contact</button>
                    <section data-settings-tab-panel="runtime">Runtime settings</section>
                    <section data-settings-tab-panel="contact" class="hidden">{error}</section>
                  </div>''')
                self.page.evaluate("fragment => location.hash = fragment", fragment)
                self.page.add_script_tag(content=script)
                self.page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
                self.assertEqual(self.page.locator('[data-settings-tab][aria-selected="true"]').get_attribute("data-settings-tab"), expected)
                self.assertTrue(self.page.locator(f'[data-settings-tab-panel="{expected}"]').is_visible())

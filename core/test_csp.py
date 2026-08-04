"""Content-Security-Policy tests.

The policy is only safe to enforce while two invariants hold: no template uses
an inline event handler, and every inline <script> carries the per-response
nonce. Both are asserted here so a future edit cannot silently re-break the
policy.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings


TEMPLATES_DIR = Path(settings.BASE_DIR) / "templates"

INLINE_HANDLER_RE = re.compile(
    r"""\bon(?:click|change|submit|load|error|input|keyup|keydown|
         focus|blur|mouseover|mouseout|dblclick)\s*=\s*["']""",
    re.IGNORECASE | re.VERBOSE,
)
# <script> blocks with a body; those with a src= are external and need no nonce.
INLINE_SCRIPT_RE = re.compile(r"<script(?P<attrs>[^>]*)>", re.IGNORECASE)

# Non-JavaScript MIME types make a <script> a data block: the browser never
# executes it, so script-src does not apply and a nonce would be meaningless.
DATA_BLOCK_TYPES = ("application/ld+json", "application/json", "text/template")


def _template_files():
    return sorted(TEMPLATES_DIR.rglob("*.html"))


class NoInlineHandlersTests(TestCase):
    def test_no_template_uses_an_inline_event_handler(self):
        offenders = []
        for path in _template_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in INLINE_HANDLER_RE.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(TEMPLATES_DIR)}:{line}")

        self.assertEqual(
            offenders,
            [],
            "Inline event handlers are blocked by the enforced CSP. Use a "
            "data- attribute plus a delegated listener in "
            f"static/js/csp-actions.js instead. Offenders: {offenders}",
        )

    def test_every_inline_script_block_carries_a_nonce(self):
        offenders = []
        for path in _template_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in INLINE_SCRIPT_RE.finditer(text):
                attrs = match.group("attrs")
                if "src=" in attrs:
                    continue  # external file, allowed by script-src 'self'
                if "csp_nonce" in attrs:
                    continue
                if any(block_type in attrs.lower() for block_type in DATA_BLOCK_TYPES):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(TEMPLATES_DIR)}:{line}")

        self.assertEqual(
            offenders,
            [],
            "Inline <script> blocks need nonce=\"{{ csp_nonce }}\" to survive "
            f"the enforced CSP. Offenders: {offenders}",
        )


class CspHeaderTests(TestCase):
    def _header(self, response):
        return response.get("Content-Security-Policy") or response.get(
            "Content-Security-Policy-Report-Only"
        )

    def test_policy_is_enforced_not_report_only(self):
        response = self.client.get("/")

        self.assertIsNotNone(response.get("Content-Security-Policy"))
        self.assertIsNone(response.get("Content-Security-Policy-Report-Only"))

    def test_script_src_carries_a_nonce(self):
        response = self.client.get("/")

        self.assertIn("nonce-", self._header(response))

    def test_nonce_differs_between_responses(self):
        first = self._header(self.client.get("/"))
        second = self._header(self.client.get("/"))

        self.assertNotEqual(first, second)

    def test_payment_gateways_may_frame_and_receive_forms(self):
        header = self._header(self.client.get("/"))

        self.assertIn("https://*.moyasar.com", header)
        self.assertIn("https://*.tamara.co", header)

    def test_dangerous_sources_are_not_allowed_for_scripts(self):
        header = self._header(self.client.get("/"))
        script_src = next(d for d in header.split(";") if d.strip().startswith("script-src"))

        self.assertNotIn("unsafe-inline", script_src)
        self.assertNotIn("unsafe-eval", script_src)

    @override_settings(CONTENT_SECURITY_POLICY_REPORT_ONLY=True)
    def test_report_only_mode_can_be_restored(self):
        response = self.client.get("/")

        self.assertIsNotNone(response.get("Content-Security-Policy-Report-Only"))
        self.assertIsNone(response.get("Content-Security-Policy"))

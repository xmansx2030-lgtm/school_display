"""Guards for the dashboard view module split.

``dashboard/views.py`` was one 5.8k-line module. It is now a facade over three
domain modules. These tests protect the two properties that make that split
safe: every URL-referenced view still resolves through ``dashboard.views``, and
no module grows back into a catch-all.
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

from django.conf import settings
from django.test import TestCase


DASHBOARD = Path(settings.BASE_DIR) / "dashboard"

VIEW_MODULES = (
    "views.py",
    "views_system.py",
    "views_billing.py",
    "views_schedule.py",
    "views_content.py",
    "view_helpers.py",
)

# Chosen a little above today's largest module so ordinary growth is fine but
# a module quietly becoming the next monolith is not.
MAX_MODULE_LINES = 1800


def _referenced_view_names() -> set[str]:
    """Every ``views.<name>`` the dashboard URLconf resolves."""
    source = io.open(DASHBOARD / "urls.py", encoding="utf-8").read()
    return {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "views"
    }


class ViewModuleStructureTests(TestCase):
    def test_every_url_view_resolves_through_the_facade(self):
        from dashboard import views

        missing = sorted(n for n in _referenced_view_names() if not hasattr(views, n))

        self.assertEqual(
            missing,
            [],
            "dashboard/urls.py references views that dashboard.views no longer "
            f"exposes. Re-export them from the module that now owns them: {missing}",
        )

    def test_referenced_views_are_callable(self):
        from dashboard import views

        not_callable = sorted(
            name
            for name in _referenced_view_names()
            if hasattr(views, name) and not callable(getattr(views, name))
        )
        self.assertEqual(not_callable, [])

    def test_no_module_grows_back_into_a_monolith(self):
        oversized = {}
        for name in VIEW_MODULES:
            path = DASHBOARD / name
            line_count = len(io.open(path, encoding="utf-8").read().splitlines())
            if line_count > MAX_MODULE_LINES:
                oversized[name] = line_count

        self.assertEqual(
            oversized,
            {},
            "A dashboard view module exceeded the size budget. Split it by domain "
            f"rather than raising the limit: {oversized}",
        )

    def test_domain_modules_own_their_views(self):
        """The split is by domain, so each module must hold its own area."""
        import dashboard.views_billing as billing
        import dashboard.views_content as content
        import dashboard.views_schedule as schedule
        import dashboard.views_system as system

        for module, expected in (
            (billing, ("my_subscription", "system_subscriptions_list")),
            (system, ("system_admin_dashboard", "system_users_list")),
            (schedule, ("days_list", "timetable_week_view")),
            (content, ("ann_list", "emergency_alert_create")),
        ):
            for name in expected:
                self.assertTrue(
                    hasattr(module, name),
                    f"{module.__name__} should own {name}",
                )

    def test_shared_layer_exports_the_private_helpers(self):
        """Star-importing the shared layer must carry the ``_`` helpers too."""
        from dashboard import view_helpers

        for name in ("_get_model", "_get_subscription_model", "_admin_order_by_existing"):
            self.assertIn(name, view_helpers.__all__)
            self.assertTrue(hasattr(view_helpers, name))

    def test_every_view_module_documents_itself(self):
        for name in VIEW_MODULES:
            source = io.open(DASHBOARD / name, encoding="utf-8").read()
            self.assertIsNotNone(
                ast.get_docstring(ast.parse(source)),
                f"dashboard/{name} lost its module docstring "
                "(check that it sits above `from __future__`).",
            )

"""The display page must serve the bundle the settings ask for."""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase, override_settings
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

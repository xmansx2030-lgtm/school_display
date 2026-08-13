"""Every page a school manager can open must render.

The dashboard grew two visual systems side by side — eight pages on the `dp-*`
premium layer and the rest on hand-rolled Tailwind — and unifying them touched
almost every template at once. A rewrite that leaves a page raising
`NoReverseMatch` or `TemplateSyntaxError` is easy to miss by hand, because most
of these pages are several clicks deep behind a live subscription.

So this walks the manager's whole surface: every GET page, plus the edit form
of every record type, plus the two list pages that take query parameters.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from notices.models import Announcement, Excellence
from schedule.models import (
    DaySchedule,
    DutyAssignment,
    Period,
    SchoolClass,
    SchoolSettings,
    Subject,
    Teacher,
)
from standby.models import StandbyAssignment
from subscriptions.models import SchoolSubscription

# Void elements never take a child, so they must not push onto the open-tag
# stack — otherwise every following element reports the wrong parent.
_VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


def _parents_of(html: str, wanted_class: str) -> list[tuple[str, str]]:
    """Return (tag, class) of the parent of every element carrying a class.

    A rendered page is not necessarily well-formed XML, so this walks the tag
    stream rather than building a DOM.
    """
    from html.parser import HTMLParser

    class Walker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, str]] = []
            self.found: list[tuple[str, str]] = []

        def handle_starttag(self, tag, attrs):
            classes = dict(attrs).get("class") or ""
            if wanted_class in classes.split() and self.stack:
                self.found.append(self.stack[-1])
            if tag not in _VOID:
                self.stack.append((tag, classes))

        def handle_startendtag(self, tag, attrs):
            classes = dict(attrs).get("class") or ""
            if wanted_class in classes.split() and self.stack:
                self.found.append(self.stack[-1])

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

    walker = Walker()
    walker.feed(html)
    return walker.found


class ManagerTemplateRenderTests(TestCase):
    """Renders the manager's pages against a school with one of everything."""

    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="مدرسة المراجعة", slug="review-school")
        cls.user = get_user_model().objects.create_user(
            username="review_manager", password="StrongPass123!"
        )
        profile = UserProfile.objects.create(user=cls.user, active_school=cls.school)
        profile.schools.add(cls.school)
        cls.settings = SchoolSettings.objects.create(name=cls.school.name, school=cls.school)

        # The tenant middleware bounces every dashboard page without a live
        # subscription, so each assertion below would only prove the redirect.
        plan = SubscriptionPlan.objects.create(
            code="review-plan",
            name="خطة المراجعة",
            price=100,
            duration_days=365,
            max_screens=3,
            max_users=4,
        )
        SchoolSubscription.objects.create(
            school=cls.school,
            plan=plan,
            starts_at=timezone.localdate(),
            status="active",
        )

        day, _ = DaySchedule.objects.get_or_create(
            settings=cls.settings, weekday=7, defaults={"periods_count": 1, "is_active": True}
        )
        Period.objects.create(day=day, index=1, starts_at="07:00", ends_at="07:45")
        cls.school_class = SchoolClass.objects.create(settings=cls.settings, name="أول/أ")
        Subject.objects.create(school=cls.school, name="الرياضيات")
        cls.teacher = Teacher.objects.create(school=cls.school, name="أ. سارة")

        cls.announcement = Announcement.objects.create(
            school=cls.school, title="اجتماع", body="نص", starts_at=timezone.now()
        )
        cls.excellence = Excellence.objects.create(
            school=cls.school, teacher_name="أ. هند", reason="تميّز", start_at=timezone.now()
        )
        cls.duty = DutyAssignment.objects.create(
            school=cls.school,
            date=timezone.localdate(),
            teacher_name="أ. سارة",
            duty_type=DutyAssignment.DUTY_SUPERVISION,
            location="الفناء",
        )
        cls.standby = StandbyAssignment.objects.create(
            school=cls.school,
            date=timezone.localdate(),
            period_index=1,
            class_name="أول/أ",
            teacher_name="أ. سارة",
        )

    def setUp(self):
        self.client.force_login(self.user)

    def assertRenders(self, url, *, name):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f"{name} → {response.status_code}")
        # Every manager page extends dashboard/_base.html, which is what pulls
        # the shared design layer in. A page that stops doing so has drifted.
        self.assertContains(response, "dashboard-premium.css")

    # ------------------------------------------------------------- every page

    def test_every_manager_page_renders(self):
        for name in (
            "dashboard:index",
            "dashboard:my_subscription",
            "dashboard:schools_overview",
            "dashboard:add_school",
            "dashboard:settings",
            "dashboard:help_getting_started",
            "dashboard:screen_list",
            "dashboard:screen_create",
            "dashboard:days_list",
            "dashboard:closures_list",
            "dashboard:timetable_day",
            "dashboard:timetable_week",
            "dashboard:ann_list",
            "dashboard:ann_create",
            "dashboard:emergency_alert_list",
            "dashboard:emergency_alert_create",
            "dashboard:occasion_templates",
            "dashboard:exc_list",
            "dashboard:exc_create",
            "dashboard:duty_list",
            "dashboard:duty_create",
            "dashboard:standby_list",
            "dashboard:standby_create",
            "dashboard:school_data",
            "dashboard:school_data_import",
            "dashboard:lessons_list",
            "dashboard:add_lesson",
            "dashboard:customer_support_tickets",
            "dashboard:customer_support_ticket_create",
            "dashboard:change_password",
            "dashboard:select_school",
        ):
            with self.subTest(page=name):
                self.assertRenders(reverse(name), name=name)

    # ------------------------------------------------------------- edit forms

    def test_every_edit_form_renders(self):
        for name, pk in (
            ("dashboard:ann_edit", self.announcement.pk),
            ("dashboard:exc_edit", self.excellence.pk),
            ("dashboard:duty_edit", self.duty.pk),
        ):
            with self.subTest(page=name):
                self.assertRenders(reverse(name, args=[pk]), name=name)

    # ---------------------------------------------------------- layout contract

    def test_the_save_bar_is_a_direct_child_of_its_form(self):
        """`.dp-actionbar` must not be wrapped, or it stops being sticky.

        A sticky box can only travel inside its containing block. Wrapped in a
        div that is exactly its own height it has nowhere to go, and the save
        bar scrolls off the screen like any static element — which is the whole
        thing it exists to avoid. Checked in a browser when this was written:
        wrapped, the bar tracked the page 1:1; unwrapped, it pinned.
        """
        pages = [
            "dashboard:ann_create",
            "dashboard:exc_create",
            "dashboard:duty_create",
            "dashboard:standby_create",
            "dashboard:screen_create",
            "dashboard:add_lesson",
            "dashboard:add_school",
            "dashboard:change_password",
            "dashboard:customer_support_ticket_create",
            "dashboard:emergency_alert_create",
        ]
        for name in pages:
            with self.subTest(page=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn("dp-actionbar", html)
                for parent_tag, parent_class in _parents_of(html, "dp-actionbar"):
                    self.assertIn(
                        "dp-form",
                        parent_class.split(),
                        f"{name}: the save bar sits inside "
                        f"<{parent_tag} class='{parent_class}'> instead of the form",
                    )

    def test_pages_with_a_swappable_shell_keep_their_script_in_content(self):
        """Emergency alerts render in two shells; only one defines `extra_js`.

        `_shell_base_template` serves `admin/admin_base.html` to a platform
        employee, and that shell has no `extra_js`/`extra_css` block — anything
        placed there is dropped without an error, leaving a form whose scope
        toggle and template autofill silently do nothing.
        """
        from django.template.loader import get_template

        admin_shell = get_template("admin/admin_base.html").template.source
        for block in ("extra_js", "extra_css"):
            if f"block {block}" in admin_shell:
                continue
            for name in ("emergency_alert_form", "emergency_alert_list"):
                source = get_template(f"dashboard/{name}.html").template.source
                self.assertNotIn(
                    "block " + block,
                    source,
                    f"dashboard/{name}.html renders in the admin shell, which "
                    f"defines no '{block}' block, so that content is dropped",
                )

    # --------------------------------------------------------------- filters

    def test_lessons_list_day_filter_uses_the_models_weekday_values(self):
        """الأحد is 7 in `WEEKDAYS`, not 0.

        The old dropdown was hand-written as 0-4, so picking الأحد filtered on a
        weekday no lesson can have and always came back empty, while الجمعة and
        السبت were missing from the list entirely.
        """
        response = self.client.get(reverse("dashboard:lessons_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="7"')
        self.assertNotContains(response, 'value="0"')

    def test_lessons_list_keeps_the_filters_it_was_given(self):
        response = self.client.get(
            reverse("dashboard:lessons_list"), {"search": "رياضيات", "day": "7"}
        )

        self.assertEqual(response.status_code, 200)
        # A search that empties its own box makes the result set unreadable —
        # the manager cannot tell what produced it.
        self.assertContains(response, 'value="رياضيات"')
        self.assertContains(response, '<option value="7" selected>')

"""Manager content operations must preserve every affected school's boundary."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import DisplayScreen, School, SubscriptionPlan, SupportTicket, SystemEmployeeProfile, UserProfile
from notices.models import EmergencyAlert
from schedule.models import DutyAssignment, SchoolSettings, Teacher
from subscriptions.models import SchoolSubscription


@override_settings(TWO_FACTOR_REQUIRED_FOR_PRIVILEGED=False)
class ManagerContentScopeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Permitted school", slug="scope-school")
        self.foreign_school = School.objects.create(name="Private foreign school", slug="foreign-scope-school")
        SchoolSettings.objects.create(school=self.school, name=self.school.name)
        self.manager = get_user_model().objects.create_user(username="scope-manager", password="TestPass123!")
        self.profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        self.profile.schools.add(self.school)
        plan = SubscriptionPlan.objects.create(code="scope-plan", name="Test", price=100, duration_days=30, max_screens=5)
        SchoolSubscription.objects.create(school=self.school, plan=plan, starts_at=timezone.localdate(), status="active")
        self.screen = DisplayScreen.objects.create(school=self.school, name="Permitted screen")
        self.foreign_screen = DisplayScreen.objects.create(school=self.foreign_school, name="Private foreign screen")
        self.client.force_login(self.manager)

    def tearDown(self):
        cache.clear()

    def shared_alert(self, *, expired=False):
        alert = EmergencyAlert.objects.create(
            kind=EmergencyAlert.KIND_URGENT, title="Shared platform alert", message="School instructions",
            created_by=self.manager,
            expires_at=timezone.now() + timedelta(minutes=-5 if expired else 30),
        )
        alert.schools.add(self.school, self.foreign_school)
        alert.screens.add(self.screen, self.foreign_screen)
        return alert

    def test_manager_cannot_cancel_an_alert_that_also_affects_an_unauthorized_school(self):
        alert = self.shared_alert()
        response = self.client.post(reverse("dashboard:emergency_alert_cancel", args=[alert.pk]))
        self.assertEqual(response.status_code, 404)
        alert.refresh_from_db()
        self.assertTrue(alert.is_active)
        self.assertIsNone(alert.cancelled_at)
        self.assertIsNone(alert.cancelled_by)

    def test_manager_cannot_delete_a_shared_expired_alert_for_another_school(self):
        alert = self.shared_alert(expired=True)
        response = self.client.post(reverse("dashboard:emergency_alert_delete", args=[alert.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(EmergencyAlert.objects.filter(pk=alert.pk).exists())

    def test_shared_alert_is_visible_without_foreign_school_or_screen_names(self):
        alert = self.shared_alert()
        response = self.client.get(reverse("dashboard:emergency_alert_list"))
        self.assertContains(response, alert.title)
        self.assertContains(response, self.school.name)
        self.assertContains(response, self.screen.name)
        self.assertNotContains(response, self.foreign_school.name)
        self.assertNotContains(response, self.foreign_screen.name)
        self.assertFalse(response.context["alerts"][0].can_manage_for_user)
        self.assertNotContains(response, reverse("dashboard:emergency_alert_cancel", args=[alert.pk]))

    def test_manager_may_manage_a_shared_alert_only_when_all_schools_are_authorized(self):
        alert = self.shared_alert()
        self.profile.schools.add(self.foreign_school)
        response = self.client.post(reverse("dashboard:emergency_alert_cancel", args=[alert.pk]))
        self.assertEqual(response.status_code, 302)
        alert.refresh_from_db()
        self.assertFalse(alert.is_active)
        self.assertEqual(alert.cancelled_by, self.manager)

    def test_hidden_foreign_screen_targets_do_not_become_all_screens_scope(self):
        alert = self.shared_alert()
        alert.screens.remove(self.screen)
        response = self.client.get(reverse("dashboard:emergency_alert_list"))
        visible = response.context["alerts"][0]
        self.assertEqual(list(visible.screens.all()), [])
        self.assertTrue(visible.has_targeted_screens)
        self.assertNotContains(response, self.foreign_screen.name)
        self.assertNotContains(response, "كل شاشات المدارس المحددة")

    def test_platform_owner_retains_shared_alert_management(self):
        alert = self.shared_alert()
        owner = get_user_model().objects.create_superuser(username="scope-owner", password="TestPass123!")
        self.client.force_login(owner)
        response = self.client.post(reverse("dashboard:emergency_alert_cancel", args=[alert.pk]))
        self.assertEqual(response.status_code, 302)
        alert.refresh_from_db()
        self.assertFalse(alert.is_active)
        self.assertEqual(alert.cancelled_by, owner)

    def test_authorized_platform_employee_retains_shared_alert_management(self):
        alert = self.shared_alert()
        employee = get_user_model().objects.create_user(username="scope-employee", password="TestPass123!", is_staff=True)
        SystemEmployeeProfile.objects.create(
            user=employee, role="custom", permission_keys=["emergency_alerts.view", "emergency_alerts.manage"],
        )
        self.client.force_login(employee)
        response = self.client.post(reverse("dashboard:emergency_alert_cancel", args=[alert.pk]))
        self.assertEqual(response.status_code, 302)
        alert.refresh_from_db()
        self.assertFalse(alert.is_active)
        self.assertEqual(alert.cancelled_by, employee)

    def test_foreign_inactive_school_is_still_protected_from_shared_mutation(self):
        alert = self.shared_alert()
        self.foreign_school.is_active = False
        self.foreign_school.save(update_fields=["is_active"])
        response = self.client.post(reverse("dashboard:emergency_alert_cancel", args=[alert.pk]))
        self.assertEqual(response.status_code, 404)
        alert.refresh_from_db()
        self.assertTrue(alert.is_active)

    def test_new_duty_requires_a_teacher_and_cannot_create_a_blank_assignment(self):
        response = self.client.post(reverse("dashboard:duty_create"), {
            "date": timezone.localdate().isoformat(), "teacher": "", "duty_type": "duty",
            "location": "Main gate", "priority": 10, "is_active": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("teacher", response.context["form"].errors)
        self.assertFalse(DutyAssignment.objects.filter(school=self.school).exists())

    def test_editing_an_unlinked_legacy_duty_keeps_its_existing_teacher_name(self):
        duty = DutyAssignment.objects.create(school=self.school, teacher_name="Legacy teacher", duty_type="duty")
        response = self.client.post(reverse("dashboard:duty_edit", args=[duty.pk]), {
            "date": timezone.localdate().isoformat(), "teacher": "", "duty_type": "supervision",
            "location": "New location", "priority": 15, "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        duty.refresh_from_db()
        self.assertEqual(duty.teacher_name, "Legacy teacher")
        self.assertEqual(duty.location, "New location")

    def test_duty_teacher_selection_rejects_another_school(self):
        teacher = Teacher.objects.create(school=self.foreign_school, name="Other teacher")
        response = self.client.post(reverse("dashboard:duty_create"), {
            "date": timezone.localdate().isoformat(), "teacher": teacher.pk, "duty_type": "duty",
            "location": "Main gate", "priority": 10, "is_active": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("teacher", response.context["form"].errors)
        self.assertFalse(DutyAssignment.objects.filter(school=self.school).exists())

    def test_a_closed_support_ticket_rejects_a_direct_comment_post(self):
        ticket = SupportTicket.objects.create(
            user=self.manager, school=self.school, subject="Resolved request", message="Details", status="closed",
        )
        response = self.client.post(reverse("dashboard:customer_support_ticket_detail", args=[ticket.pk]), {"message": "Late comment"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ticket.comments.exists())
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "closed")

    def test_an_open_support_ticket_accepts_its_owners_comment(self):
        ticket = SupportTicket.objects.create(
            user=self.manager, school=self.school, subject="Open request", message="Details", status="open",
        )
        response = self.client.post(reverse("dashboard:customer_support_ticket_detail", args=[ticket.pk]), {"message": "More details"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ticket.comments.get().message, "More details")
        self.assertEqual(ticket.comments.get().user, self.manager)

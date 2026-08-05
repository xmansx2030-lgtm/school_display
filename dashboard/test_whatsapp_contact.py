from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from schedule.models import SchoolSettings
from subscriptions.models import SchoolSubscription

from dashboard.context_processors import school_whatsapp_contact


class SchoolWhatsAppContactTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(
            name="مدرسة واتساب النموذجية",
            slug="whatsapp-contact-school",
        )
        self.user = get_user_model().objects.create_user(
            username="whatsapp_manager",
            password="StrongPass123!",
        )
        self.profile = UserProfile.objects.create(
            user=self.user,
            active_school=self.school,
        )
        self.profile.schools.add(self.school)
        SchoolSettings.objects.create(name=self.school.name, school=self.school)
        self.plan = SubscriptionPlan.objects.create(
            code="whatsapp-contact-plan",
            name="الباقة السنوية",
            price=990,
            duration_days=365,
            max_screens=1,
        )
        self.client.force_login(self.user)

    def test_floating_button_prefills_school_and_active_subscription(self):
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )

        response = self.client.get(reverse("dashboard:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "dashboard-whatsapp-float")
        self.assertContains(response, "966537720207")
        whatsapp_url = response.context["school_whatsapp_url"]
        message = parse_qs(urlparse(whatsapp_url).query)["text"][0]
        self.assertTrue(
            message.startswith(
                "المنصة: منصة لوحة العرض الذكية\n"
                "اسم المدرسة: مدرسة واتساب النموذجية\n"
                "حالة الاشتراك: ساري"
            )
        )
        self.assertIn("الباقة: الباقة السنوية", message)

    def test_floating_button_reports_school_without_subscription(self):
        response = self.client.get(reverse("dashboard:add_school"))

        self.assertEqual(response.status_code, 200)
        whatsapp_url = response.context["school_whatsapp_url"]
        message = parse_qs(urlparse(whatsapp_url).query)["text"][0]
        self.assertTrue(
            message.startswith(
                "المنصة: منصة لوحة العرض الذكية\n"
                "اسم المدرسة: مدرسة واتساب النموذجية\n"
                "حالة الاشتراك: بدون اشتراك"
            )
        )

    def test_system_admin_does_not_receive_school_whatsapp_button(self):
        admin = get_user_model().objects.create_superuser(
            username="whatsapp_admin",
            email="admin@example.com",
            password="StrongPass123!",
        )
        request = RequestFactory().get(reverse("dashboard:system_admin_dashboard"))
        request.user = admin
        request.school = None

        self.assertEqual(school_whatsapp_contact(request), {})

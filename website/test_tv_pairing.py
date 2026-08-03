from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import DisplayPairingSession, DisplayScreen, School, SubscriptionPlan, UserProfile
from display.pairing import create_pairing_session
from schedule.models import SchoolSettings
from subscriptions.models import SchoolSubscription


@override_settings(
    DISPLAY_PAIRING_TTL_SEC=600,
    DISPLAY_PAIRING_START_LIMIT=20,
    DISPLAY_PAIRING_CODE_ATTEMPTS=8,
)
class TvPairingFlowTests(TestCase):
    device_id = "tv-1234567890abcdef1234567890abcdef"

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة الاقتران", slug="pairing-school")
        SchoolSettings.objects.create(name=self.school.name, school=self.school)
        self.screen = DisplayScreen.objects.create(name="الشاشة الرئيسية", school=self.school)
        self.user = get_user_model().objects.create_user(
            username="pairing_manager",
            password="StrongPass123!",
        )
        self.profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        self.profile.schools.add(self.school)
        plan = SubscriptionPlan.objects.create(
            code="pairing-plan",
            name="باقة اختبار الاقتران",
            price=100,
            duration_days=365,
            max_screens=5,
        )
        SchoolSubscription.objects.create(
            school=self.school,
            plan=plan,
            starts_at=timezone.localdate(),
            status="active",
        )

    def _start_pairing(self):
        response = self.client.post(
            reverse("website:tv_pairing_start"),
            {"device_id": self.device_id},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_tv_entry_page_is_public_and_contains_mobile_instructions(self):
        response = self.client.get(reverse("website:tv_pairing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "امسح الرمز من جوالك")
        self.assertContains(response, reverse("website:tv_pairing_start"))
        self.assertNotContains(response, self.screen.token)

    def test_tv_can_create_one_time_pairing_and_render_qr(self):
        data = self._start_pairing()

        self.assertRegex(data["user_code"], r"^\d{6}$")
        pairing = DisplayPairingSession.objects.get(pk=data["pairing_id"])
        self.assertEqual(pairing.device_id, self.device_id)
        self.assertNotEqual(pairing.device_secret_hash, data["device_secret"])
        qr_response = self.client.get(data["qr_url"])
        self.assertEqual(qr_response.status_code, 200)
        self.assertEqual(qr_response["Content-Type"], "image/png")
        self.assertGreater(len(qr_response.content), 500)
        with Image.open(BytesIO(qr_response.content)) as qr_image:
            self.assertEqual(qr_image.mode, "RGB")

    def test_start_rejects_malformed_device_identifier(self):
        response = self.client.post(
            reverse("website:tv_pairing_start"),
            {"device_id": "short"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "invalid_device")

    def test_status_requires_the_tv_secret_and_expires_session(self):
        data = self._start_pairing()
        pairing = DisplayPairingSession.objects.get(pk=data["pairing_id"])
        status_url = reverse("website:tv_pairing_status", args=[pairing.pk])

        invalid = self.client.post(status_url, {"device_secret": "incorrect-secret"})
        self.assertEqual(invalid.status_code, 404)

        pairing.expires_at = timezone.now() - timedelta(seconds=1)
        pairing.save(update_fields=["expires_at"])
        expired = self.client.post(status_url, {"device_secret": data["device_secret"]})
        self.assertEqual(expired.status_code, 200)
        self.assertEqual(expired.json()["status"], DisplayPairingSession.STATUS_EXPIRED)

    def test_manager_can_find_code_with_arabic_digits(self):
        data = self._start_pairing()
        arabic_code = data["user_code"].translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dashboard:screen_pairing"),
            {"user_code": arabic_code},
        )

        self.assertRedirects(
            response,
            reverse("dashboard:screen_pairing_confirm", args=[data["pairing_id"]]),
            fetch_redirect_response=False,
        )

    def test_manager_approval_binds_exact_tv_and_tv_receives_display_url(self):
        data = self._start_pairing()
        pairing = DisplayPairingSession.objects.get(pk=data["pairing_id"])
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dashboard:screen_pairing_confirm", args=[pairing.pk]),
            {"screen_id": self.screen.pk},
        )
        self.assertRedirects(
            response,
            reverse("dashboard:screen_pairing_confirm", args=[pairing.pk]),
            fetch_redirect_response=False,
        )

        pairing.refresh_from_db()
        self.screen.refresh_from_db()
        self.assertEqual(pairing.status, DisplayPairingSession.STATUS_APPROVED)
        self.assertEqual(pairing.screen, self.screen)
        self.assertEqual(pairing.approved_by, self.user)
        self.assertEqual(self.screen.bound_device_id, self.device_id)

        status_response = self.client.post(
            reverse("website:tv_pairing_status", args=[pairing.pk]),
            {"device_secret": data["device_secret"]},
        )
        payload = status_response.json()
        self.assertEqual(payload["status"], DisplayPairingSession.STATUS_APPROVED)
        self.assertEqual(payload["screen_name"], self.screen.name)
        self.assertTrue(payload["display_url"].startswith(f"/s/{self.screen.short_code}/#pair="))
        self.assertIn(self.device_id, payload["display_url"])

    def test_replacing_an_existing_device_requires_explicit_confirmation(self):
        self.screen.bound_device_id = "tv-existing-device-1234567890"
        self.screen.bound_at = timezone.now()
        self.screen.save(update_fields=["bound_device_id", "bound_at"])
        pairing, _secret = create_pairing_session(self.device_id)
        self.client.force_login(self.user)
        confirm_url = reverse("dashboard:screen_pairing_confirm", args=[pairing.pk])

        rejected = self.client.post(confirm_url, {"screen_id": self.screen.pk})
        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, "فعّل خيار الاستبدال")
        self.screen.refresh_from_db()
        self.assertEqual(self.screen.bound_device_id, "tv-existing-device-1234567890")

        approved = self.client.post(
            confirm_url,
            {"screen_id": self.screen.pk, "replace_existing": "1"},
        )
        self.assertEqual(approved.status_code, 302)
        self.screen.refresh_from_db()
        self.assertEqual(self.screen.bound_device_id, self.device_id)

    def test_manager_cannot_pair_a_screen_from_another_school(self):
        other_school = School.objects.create(name="مدرسة أخرى", slug="other-pairing-school")
        other_screen = DisplayScreen.objects.create(name="شاشة خارجية", school=other_school)
        pairing, _secret = create_pairing_session(self.device_id)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("dashboard:screen_pairing_confirm", args=[pairing.pk]),
            {"screen_id": other_screen.pk},
        )

        self.assertEqual(response.status_code, 404)
        pairing.refresh_from_db()
        self.assertEqual(pairing.status, DisplayPairingSession.STATUS_PENDING)

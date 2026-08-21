from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import School, UserProfile, UserSessionState


@override_settings(DISPLAY_REQUIRE_ACTIVE_SUBSCRIPTION=False)
class SingleSessionPolicyTests(TestCase):
    def setUp(self):
        self.password = "StrongPass123!"
        self.user = get_user_model().objects.create_user(
            username="single_session_manager",
            password=self.password,
        )
        self.school = School.objects.create(name="مدرسة الجلسة الواحدة", slug="single-session-school")
        profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        profile.schools.add(self.school)
        self.computer = Client()
        self.mobile = Client()

    def _login(self, client):
        return client.post(
            reverse("dashboard:login"),
            {"username": self.user.username, "password": self.password},
        )

    def test_mobile_login_immediately_replaces_the_computer_session(self):
        self.assertEqual(self._login(self.computer).status_code, 302)
        computer_key = self.computer.session.session_key
        self.assertTrue(Session.objects.filter(session_key=computer_key).exists())

        self.assertEqual(self._login(self.mobile).status_code, 302)
        mobile_key = self.mobile.session.session_key

        self.assertNotEqual(computer_key, mobile_key)
        self.assertFalse(Session.objects.filter(session_key=computer_key).exists())
        self.assertTrue(Session.objects.filter(session_key=mobile_key).exists())
        self.assertEqual(
            UserSessionState.objects.get(user=self.user).active_session_key,
            mobile_key,
        )

        replaced = self.computer.get(reverse("dashboard:index"))
        self.assertRedirects(
            replaced,
            f'{reverse("dashboard:login")}?reason=session_replaced',
            fetch_redirect_response=False,
        )

        notice = self.computer.get(replaced["Location"])
        self.assertContains(notice, "تم إنهاء هذه الجلسة لحماية الحساب")
        self.assertContains(notice, "يسمح النظام بجلسة واحدة فقط")

        # The newest browser remains authenticated.
        self.assertEqual(int(self.mobile.session["_auth_user_id"]), self.user.pk)

    def test_replaced_session_gets_a_machine_readable_api_response(self):
        self._login(self.computer)
        computer_key = self.computer.session.session_key
        self._login(self.mobile)

        # Restore the stale cookie after the second login so this request
        # represents an already-open computer tab making an API call.
        self.computer.cookies["sessionid"] = computer_key
        response = self.computer.get("/api/display/status/", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "session_replaced")
        self.assertEqual(response["X-Session-Replaced"], "1")

    def test_password_change_keeps_the_rotated_current_session_active(self):
        self._login(self.mobile)

        response = self.mobile.post(
            reverse("dashboard:change_password"),
            {
                "old_password": self.password,
                "new_password1": "NewStrongPass456!",
                "new_password2": "NewStrongPass456!",
                "next": reverse("dashboard:change_password"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            UserSessionState.objects.get(user=self.user).active_session_key,
            self.mobile.session.session_key,
        )
        self.assertEqual(int(self.mobile.session["_auth_user_id"]), self.user.pk)

"""Tests for signed email-ownership verification."""

from __future__ import annotations

import time

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.email_verification import (
    EmailVerificationError,
    make_token,
    mark_verified,
    user_email_is_verified,
    verify_token,
)
from core.models import School, UserProfile


class EmailVerificationTokenTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="verify_user",
            password="StrongPass123!",
            email="Owner@Example.com",
        )
        self.profile = UserProfile.objects.create(user=self.user)

    def test_round_trip_returns_the_same_user(self):
        self.assertEqual(verify_token(make_token(self.user)), self.user)

    def test_tampered_token_is_rejected(self):
        with self.assertRaises(EmailVerificationError):
            verify_token(make_token(self.user) + "x")

    def test_empty_token_is_rejected(self):
        with self.assertRaises(EmailVerificationError):
            verify_token("")

    def test_token_dies_when_the_address_changes(self):
        """A link issued for the old address must not verify a new one."""
        token = make_token(self.user)
        self.user.email = "someone-else@example.com"
        self.user.save(update_fields=["email"])

        with self.assertRaises(EmailVerificationError):
            verify_token(token)

    def test_token_expires(self):
        token = make_token(self.user)
        with override_settings(EMAIL_VERIFICATION_TIMEOUT=1):
            time.sleep(1.1)
            with self.assertRaises(EmailVerificationError):
                verify_token(token)

    def test_inactive_account_cannot_verify(self):
        token = make_token(self.user)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        with self.assertRaises(EmailVerificationError):
            verify_token(token)

    def test_mark_verified_is_idempotent(self):
        self.assertTrue(mark_verified(self.user))
        self.assertFalse(mark_verified(self.user))

    def test_superusers_are_treated_as_verified(self):
        admin = get_user_model().objects.create_superuser(
            username="root",
            password="StrongPass123!",
            email="root@example.com",
        )
        self.assertTrue(user_email_is_verified(admin))

    def test_anonymous_is_never_verified(self):
        self.assertFalse(user_email_is_verified(None))


class VerifyEmailViewTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة التوثيق", slug="verify-school")
        self.user = get_user_model().objects.create_user(
            username="verify_view_user",
            password="StrongPass123!",
            email="school@example.com",
        )
        self.profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        self.profile.schools.add(self.school)

    def _url(self, token: str) -> str:
        return reverse("website:verify_email", kwargs={"token": token})

    def test_valid_link_marks_the_account_verified(self):
        response = self.client.get(self._url(make_token(self.user)))

        self.profile.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(self.profile.email_verified_at)
        self.assertContains(response, "تم تأكيد بريدك بنجاح")

    def test_second_visit_reports_already_verified(self):
        token = make_token(self.user)
        self.client.get(self._url(token))

        response = self.client.get(self._url(token))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مؤكد مسبقاً")

    def test_invalid_link_explains_the_failure(self):
        response = self.client.get(self._url("clearly-not-a-token"))

        self.profile.refresh_from_db()
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.profile.email_verified_at)
        self.assertContains(response, "تعذّر تأكيد البريد", status_code=400)

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from unittest.mock import patch

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from core.models import School, SubscriptionPlan, UserProfile
from schedule.models import SchoolSettings
from subscriptions.models import (
    SchoolSubscription,
    SubscriptionInvoice,
    SubscriptionEmailNotification,
    SubscriptionPaymentOperation,
    SubscriptionScreenAddon,
)
from dashboard.forms import SchoolSubscriptionForm
from subscriptions.utils import school_effective_max_screens, school_has_active_subscription
from subscriptions.email_notifications import (
    enqueue_expiry_email_reminders,
    process_pending_email_notifications,
)
from subscriptions.invoicing import reconcile_missing_invoices


class SubscriptionBusinessRulesTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.school = School.objects.create(name="مدرسة الاشتراك", slug="subscription-school")
        self.plan = SubscriptionPlan.objects.create(
            code="professional",
            name="الاحترافية",
            price=Decimal("500.00"),
            duration_days=365,
            max_screens=3,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=self.today - timedelta(days=10),
            ends_at=self.today + timedelta(days=30),
            status="active",
        )

    def test_active_subscription_includes_both_date_boundaries(self):
        self.subscription.starts_at = self.today
        self.subscription.ends_at = self.today
        self.subscription.save(update_fields=["starts_at", "ends_at"])

        self.assertTrue(school_has_active_subscription(self.school.pk, on_date=self.today))

    def test_cancellation_reason_and_timestamp_are_persisted(self):
        self.subscription.status = "cancelled"
        self.subscription.closure_reason = "budget"
        self.subscription.closure_notes = "لم تعتمد الميزانية"
        self.subscription.save(update_fields=["status", "closure_reason", "closure_notes"])

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.closure_reason, "budget")
        self.assertIsNotNone(self.subscription.closed_at)

    def test_admin_form_requires_a_closure_reason_for_cancelled_subscription(self):
        form = SchoolSubscriptionForm(
            data={
                "school": self.school.pk,
                "plan": self.plan.pk,
                "starts_at": self.subscription.starts_at,
                "status": "cancelled",
                "closure_reason": "",
                "closure_notes": "",
                "notes": "",
                "payment_method": "",
            },
            instance=self.subscription,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("closure_reason", form.errors)

    def _create_form(self, **overrides):
        data = {
            "school": self.school.pk,
            "plan": self.plan.pk,
            "starts_at": self.today,
            "status": "active",
            "closure_reason": "",
            "closure_notes": "",
            "notes": "",
            "payment_method": "",
        }
        data.update(overrides)
        return SchoolSubscriptionForm(data=data)

    def test_a_paid_subscription_cannot_be_created_without_a_payment_method(self):
        """The whole invoice chain hangs off the payment operation.

        This rule existed but was written as ``raise ValidationError`` inside a
        bare ``except Exception``, so it caught the very error it raised and the
        form accepted paid subscriptions with no payment method — leaving the
        school billed in the UI but never invoiced or emailed.
        """
        form = self._create_form(payment_method="")

        self.assertFalse(form.is_valid())
        self.assertIn("payment_method", form.errors)

    def test_a_paid_subscription_is_accepted_once_a_payment_method_is_given(self):
        form = self._create_form(payment_method="bank_transfer")

        self.assertTrue(form.is_valid(), form.errors)

    def test_a_free_plan_still_needs_no_payment_method(self):
        free = SubscriptionPlan.objects.create(
            code="free-plan",
            name="مجانية",
            price=Decimal("0.00"),
            duration_days=14,
            max_screens=1,
        )
        form = self._create_form(plan=free.pk, payment_method="")

        self.assertTrue(form.is_valid(), form.errors)

    def test_paid_screen_addons_extend_limit_but_pending_addons_do_not(self):
        SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=2,
            status="paid",
            starts_at=self.today,
            ends_at=self.today + timedelta(days=10),
        )
        SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=20,
            status="pending",
            starts_at=self.today,
            ends_at=self.today + timedelta(days=10),
        )

        self.assertEqual(school_effective_max_screens(self.school.pk, on_date=self.today), 5)

    def test_expired_addon_does_not_extend_the_screen_limit(self):
        SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=2,
            status="paid",
            starts_at=self.today - timedelta(days=20),
            ends_at=self.today - timedelta(days=1),
        )

        self.assertEqual(school_effective_max_screens(self.school.pk, on_date=self.today), 3)

    def test_auto_screen_addon_uses_flat_commercial_price_per_screen(self):
        monthly = SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=2,
            pricing_strategy="auto_bundle",
            pricing_cycle="monthly",
            starts_at=self.today,
        )
        semiannual = SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=2,
            pricing_strategy="auto_bundle",
            pricing_cycle="semiannual",
            starts_at=self.today,
        )
        annual = SubscriptionScreenAddon.objects.create(
            subscription=self.subscription,
            screens_added=2,
            pricing_strategy="auto_bundle",
            pricing_cycle="annual",
            starts_at=self.today,
        )

        self.assertEqual(monthly.bundle_price, Decimal("120.00"))
        self.assertEqual(semiannual.bundle_price, Decimal("720.00"))
        self.assertEqual(annual.bundle_price, Decimal("1200.00"))
        self.assertEqual(annual.total_price, Decimal("1200.00"))

    def test_payment_operation_creates_an_immutable_invoice_snapshot(self):
        operation = SubscriptionPaymentOperation.objects.create(
            school=self.school,
            subscription=self.subscription,
            plan=self.plan,
            amount=Decimal("500.00"),
            method="bank_transfer",
            source="admin_manual",
        )

        invoice = operation.invoice
        original_snapshot = invoice.html_snapshot
        self.school.name = "اسم المدرسة بعد إصدار الفاتورة"
        self.school.save(update_fields=["name"])
        invoice.refresh_from_db()

        self.assertTrue(invoice.invoice_number.startswith("INV-"))
        self.assertEqual(invoice.amount, Decimal("500.00"))
        self.assertEqual(invoice.school, self.school)
        self.assertIn("مدرسة الاشتراك", invoice.html_snapshot)
        self.assertEqual(invoice.html_snapshot, original_snapshot)


class InvoiceTenantIsolationTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.school_a = School.objects.create(name="المدرسة أ", slug="school-a")
        self.school_b = School.objects.create(name="المدرسة ب", slug="school-b")
        plan = SubscriptionPlan.objects.create(code="invoice-plan", name="فاتورة", price=100, max_screens=2)
        self.subscription_a = SchoolSubscription.objects.create(
            school=self.school_a,
            plan=plan,
            starts_at=today,
            ends_at=today + timedelta(days=30),
            status="active",
        )
        subscription_b = SchoolSubscription.objects.create(
            school=self.school_b,
            plan=plan,
            starts_at=today,
            ends_at=today + timedelta(days=30),
            status="active",
        )
        operation_b = SubscriptionPaymentOperation.objects.create(
            school=self.school_b,
            subscription=subscription_b,
            plan=plan,
            amount=100,
            method="bank_transfer",
        )
        self.invoice_b = operation_b.invoice

        self.user = get_user_model().objects.create_user(username="school_a_manager", password="StrongPass123!")
        profile = UserProfile.objects.create(user=self.user, active_school=self.school_a)
        profile.schools.add(self.school_a)

    def test_school_cannot_open_another_schools_invoice_by_guessing_id(self):
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("dashboard:subscription_invoice_view", kwargs={"pk": self.invoice_b.pk})
        )

        self.assertEqual(response.status_code, 404)


@override_settings(
    TRANSACTIONAL_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
    EMAIL_NOTIFICATION_BATCH_SIZE=20,
    EMAIL_NOTIFICATION_MAX_ATTEMPTS=3,
    SITE_BASE_URL="https://school-display.com",
    TWO_FACTOR_REQUIRED_FOR_PRIVILEGED=False,
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "subscription-invoice-flow-tests",
        }
    },
)
class SubscriptionInvoiceOnCreationTests(TestCase):
    """Creating a paid subscription must bill the school and tell them.

    Covers the whole chain end to end — admin form → payment operation →
    invoice → outbox row → delivered message — because every previous break in
    it was silent: the subscription saved fine and nobody noticed the invoice
    had never been issued.
    """

    def setUp(self):
        self.today = timezone.localdate()
        self.admin = get_user_model().objects.create_superuser(
            username="billing_admin",
            email="billing-admin@example.com",
            password="StrongPass123!",
        )
        self.school = School.objects.create(name="مدرسة الفوترة", slug="billing-school")
        self.manager = get_user_model().objects.create_user(
            username="billing_manager",
            email="buyer@example.com",
            password="StrongPass123!",
        )
        profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        profile.schools.add(self.school)
        self.plan = SubscriptionPlan.objects.create(
            code="billing-annual",
            name="الباقة السنوية",
            price=Decimal("1070.00"),
            duration_days=365,
            max_screens=1,
            card_features="ميزة أولى\nميزة ثانية",
        )
        self.client.force_login(self.admin)

    def _post_create(self, **overrides):
        data = {
            "school": self.school.pk,
            "plan": self.plan.pk,
            "starts_at": self.today.isoformat(),
            "status": "active",
            "closure_reason": "",
            "closure_notes": "",
            "notes": "",
            "payment_method": "bank_transfer",
        }
        data.update(overrides)
        return self.client.post(reverse("dashboard:system_subscription_create"), data)

    def test_creating_a_paid_subscription_issues_and_sends_the_invoice(self):
        response = self._post_create()
        self.assertEqual(response.status_code, 302)

        subscription = SchoolSubscription.objects.get(school=self.school)
        operation = SubscriptionPaymentOperation.objects.get(subscription=subscription)
        self.assertEqual(operation.amount, Decimal("1070.00"))
        self.assertEqual(operation.method, "bank_transfer")

        invoice = SubscriptionInvoice.objects.get(operation=operation)
        self.assertTrue(invoice.html_snapshot)

        notification = SubscriptionEmailNotification.objects.get(invoice=invoice)
        self.assertEqual(notification.recipient, "buyer@example.com")

        result = process_pending_email_notifications()

        self.assertEqual(result.sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["buyer@example.com"])
        self.assertIn(invoice.invoice_number, message.subject)
        # The plan's full terms travel with the invoice, not just its price.
        body = message.body
        self.assertIn(self.plan.name, body)
        self.assertIn("1,070.00", body)
        self.assertIn("سنة كاملة", body)
        self.assertIn("شاشة واحدة", body)
        self.assertIn("ميزة ثانية", body)

    def test_a_paid_subscription_is_refused_without_a_payment_method(self):
        response = self._post_create(payment_method="")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SchoolSubscription.objects.exists())
        self.assertFalse(SubscriptionInvoice.objects.exists())
        self.assertFalse(SubscriptionEmailNotification.objects.exists())

    def test_a_free_plan_is_created_without_billing_the_school(self):
        free = SubscriptionPlan.objects.create(
            code="billing-free",
            name="تجربة مجانية",
            price=Decimal("0.00"),
            duration_days=14,
            max_screens=1,
        )

        response = self._post_create(plan=free.pk, payment_method="")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SchoolSubscription.objects.filter(plan=free).exists())
        self.assertFalse(SubscriptionPaymentOperation.objects.exists())
        self.assertFalse(SubscriptionInvoice.objects.exists())
        self.assertEqual(len(mail.outbox), 0)


@override_settings(
    TRANSACTIONAL_EMAIL_ENABLED=True,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
    EMAIL_NOTIFICATION_BATCH_SIZE=20,
    EMAIL_NOTIFICATION_MAX_ATTEMPTS=3,
    EMAIL_SUBSCRIPTION_EXPIRY_DAYS=(7, 1, 0),
    SITE_BASE_URL="https://school-display.com",
)
class SubscriptionEmailNotificationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.school = School.objects.create(name="مدرسة البريد", slug="email-school")
        self.plan = SubscriptionPlan.objects.create(
            code="email-plan",
            name="الخطة البريدية",
            price=Decimal("500.00"),
            duration_days=30,
            max_screens=2,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=self.today,
            ends_at=self.today + timedelta(days=7),
            status="active",
        )
        self.user = get_user_model().objects.create_user(
            username="email_manager",
            email="manager@example.com",
            password="StrongPass123!",
        )
        profile = UserProfile.objects.create(user=self.user, active_school=self.school)
        profile.schools.add(self.school)
        inactive_user = get_user_model().objects.create_user(
            username="inactive_email_manager",
            email="inactive-manager@example.com",
            password="StrongPass123!",
            is_active=False,
        )
        inactive_profile = UserProfile.objects.create(
            user=inactive_user,
            active_school=self.school,
        )
        inactive_profile.schools.add(self.school)

    def test_new_invoice_is_queued_and_delivered_with_attachment(self):
        operation = SubscriptionPaymentOperation.objects.create(
            school=self.school,
            subscription=self.subscription,
            plan=self.plan,
            amount=Decimal("500.00"),
            method="bank_transfer",
        )

        notification = SubscriptionEmailNotification.objects.get(
            event_type=SubscriptionEmailNotification.EventType.INVOICE
        )
        self.assertEqual(notification.recipient, "manager@example.com")
        self.assertEqual(notification.invoice, operation.invoice)

        result = process_pending_email_notifications()

        self.assertEqual(result.sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(operation.invoice.invoice_number, mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["manager@example.com"])
        self.assertTrue(
            any(
                attachment[0].startswith("invoice-") and attachment[2] == "text/html"
                for attachment in mail.outbox[0].attachments
            )
        )
        notification.refresh_from_db()
        self.assertEqual(notification.status, SubscriptionEmailNotification.Status.SENT)

    def test_queued_email_is_skipped_if_account_becomes_inactive(self):
        operation = SubscriptionPaymentOperation.objects.create(
            school=self.school,
            subscription=self.subscription,
            plan=self.plan,
            amount=Decimal("500.00"),
            method="bank_transfer",
        )
        notification = SubscriptionEmailNotification.objects.get(
            invoice=operation.invoice,
        )
        self.user.is_active = False
        self.user.save(update_fields=("is_active",))

        result = process_pending_email_notifications()

        notification.refresh_from_db()
        self.assertEqual(result.sent, 0)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(
            notification.status,
            SubscriptionEmailNotification.Status.SKIPPED,
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_worker_reconciles_a_paid_operation_created_without_an_invoice(self):
        operations = SubscriptionPaymentOperation.objects.bulk_create(
            [
                SubscriptionPaymentOperation(
                    school=self.school,
                    subscription=self.subscription,
                    plan=self.plan,
                    amount=Decimal("500.00"),
                    method="bank_transfer",
                )
            ]
        )
        operation = operations[0]
        self.assertFalse(SubscriptionInvoice.objects.filter(operation=operation).exists())

        created = reconcile_missing_invoices()

        self.assertEqual(created, 1)
        invoice = SubscriptionInvoice.objects.get(operation=operation)
        self.assertTrue(invoice.html_snapshot)
        self.assertTrue(
            SubscriptionEmailNotification.objects.filter(
                invoice=invoice,
                recipient="manager@example.com",
            ).exists()
        )

    def test_expiry_reminder_is_deduplicated_and_delivered(self):
        first_count = enqueue_expiry_email_reminders(on_date=self.today)
        second_count = enqueue_expiry_email_reminders(on_date=self.today)

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        notification = SubscriptionEmailNotification.objects.get(
            event_type=SubscriptionEmailNotification.EventType.EXPIRY
        )
        self.assertEqual(notification.reminder_days, 7)

        result = process_pending_email_notifications()

        self.assertEqual(result.sent, 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("متبقي 7 أيام", mail.outbox[0].subject)


class TamaraIsRemovedTests(TestCase):
    """تمارا أُوقفت نهائياً. هذه الاختبارات تصرخ إن عاد منها شيء.

    الإيقاف السابق كان إخفاءً بمفتاح: الكود والجداول والمسارات باقية، ورفع
    المفتاح وحده يعيدها. الآن حُذفت من الشجرة، فالحارس تغيّر معها — لم يعد
    يسأل "هل هي مخفية؟" بل "هل اختفت فعلاً؟".
    """

    def setUp(self):
        self.school = School.objects.create(name="مدرسة الإيقاف", slug="removed-school")
        self.plan = SubscriptionPlan.objects.create(
            code="removed-plan",
            name="باقة",
            price=Decimal("500.00"),
            duration_days=365,
            max_screens=2,
        )
        self.manager = get_user_model().objects.create_user(
            username="removed_manager", password="StrongPass123!", email="h@example.com"
        )
        profile = UserProfile.objects.create(user=self.manager, active_school=self.school)
        profile.schools.add(self.school)
        SchoolSettings.objects.create(school=self.school, name=self.school.name)
        SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )

    def test_no_tamara_settings_survive(self):
        leftovers = [name for name in dir(settings) if "TAMARA" in name]
        self.assertEqual(leftovers, [])

    def test_no_tamara_routes_survive(self):
        for name in (
            "subscriptions:tamara_start",
            "subscriptions:tamara_webhook",
            "subscriptions:tamara_success",
            "subscriptions:tamara_failure",
            "subscriptions:tamara_cancel",
        ):
            with self.assertRaises(NoReverseMatch):
                reverse(name)

    def test_no_tamara_module_survives(self):
        for module in (
            "subscriptions.tamara",
            "subscriptions.tamara_views",
            "subscriptions.tamara_processing",
        ):
            with self.assertRaises(ImportError):
                import_module(module)

    def test_subscription_page_mentions_nothing(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard:my_subscription"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "تمارا")
        self.assertNotContains(response, "tamara")

    def test_public_pricing_page_mentions_nothing(self):
        response = self.client.get(reverse("website:subscriptions"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "تمارا")
        self.assertNotContains(response, "tamara")

    def test_card_payment_is_still_offered(self):
        """Removing one provider must not take the whole checkout down with it."""
        self.client.force_login(self.manager)
        with override_settings(
            MOYASAR_ENABLED=True,
            MOYASAR_LIVE_MODE=True,
            MOYASAR_PUBLISHABLE_KEY="pk_live_x",
            MOYASAR_SECRET_KEY="sk_live_x",
        ):
            response = self.client.get(reverse("dashboard:my_subscription"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["moyasar_available"])


class InvoiceableMethodTests(TestCase):
    """الفاتورة تصدر عن ميسر أو التحويل البنكي فقط."""

    def setUp(self):
        self.school = School.objects.create(name="مدرسة الطرق", slug="methods-school")
        self.plan = SubscriptionPlan.objects.create(
            code="methods-plan",
            name="باقة",
            price=Decimal("500.00"),
            duration_days=365,
            max_screens=1,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            starts_at=timezone.localdate(),
            status="active",
        )

    def _operation(self, method):
        return SubscriptionPaymentOperation.objects.create(
            school=self.school,
            subscription=self.subscription,
            plan=self.plan,
            amount=Decimal("500.00"),
            method=method,
        )

    def test_only_two_methods_are_offered(self):
        self.assertEqual(
            [code for code, _label in SubscriptionPaymentOperation.METHOD_CHOICES],
            ["bank_transfer", "moyasar"],
        )

    def test_bank_transfer_issues_an_invoice(self):
        operation = self._operation("bank_transfer")
        self.assertTrue(SubscriptionInvoice.objects.filter(operation=operation).exists())

    def test_moyasar_issues_an_invoice(self):
        operation = self._operation("moyasar")
        self.assertTrue(SubscriptionInvoice.objects.filter(operation=operation).exists())

    def test_a_retired_method_issues_nothing(self):
        """A row written straight to the ORM bypasses choice validation.

        ``objects.create()`` never checks ``choices``, so a leftover integration
        writing method="tamara" would otherwise still mint an invoice.
        """
        operation = self._operation("tamara")

        self.assertFalse(SubscriptionInvoice.objects.filter(operation=operation).exists())
        self.assertFalse(SubscriptionEmailNotification.objects.exists())

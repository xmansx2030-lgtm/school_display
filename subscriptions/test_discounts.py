"""اختبارات منطق أكواد الخصم: الصلاحية، الحساب، وتسجيل الاستخدام."""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from core.models import School, SubscriptionPlan
from .discounts import DiscountError, quote_discount, record_redemption
from .models import DiscountCode, DiscountRedemption, MoyasarCheckout


class DiscountQuoteTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة الخصم", slug="discount-school")
        self.other_school = School.objects.create(name="مدرسة أخرى", slug="other-school")

    def _code(self, **overrides):
        defaults = dict(
            code="SAVE20",
            discount_type=DiscountCode.TYPE_PERCENT,
            percent=Decimal("20"),
            max_uses=5,
        )
        defaults.update(overrides)
        return DiscountCode.objects.create(**defaults)

    def test_percent_discount_is_computed_on_total(self):
        self._code()
        q = quote_discount("save20", self.school, Decimal("1000.00"))
        self.assertEqual(q.discount_amount, Decimal("200.00"))
        self.assertEqual(q.final_total, Decimal("800.00"))

    def test_fixed_amount_discount_never_exceeds_total(self):
        self._code(
            code="FLAT150",
            discount_type=DiscountCode.TYPE_AMOUNT,
            percent=None,
            amount=Decimal("150.00"),
        )
        q = quote_discount("FLAT150", self.school, Decimal("100.00"))
        self.assertEqual(q.discount_amount, Decimal("100.00"))
        self.assertEqual(q.final_total, Decimal("0.00"))

    def test_unknown_code_is_rejected(self):
        with self.assertRaises(DiscountError):
            quote_discount("NOPE", self.school, Decimal("100"))

    def test_inactive_code_is_rejected(self):
        self._code(is_active=False)
        with self.assertRaises(DiscountError):
            quote_discount("SAVE20", self.school, Decimal("100"))

    def test_code_outside_window_is_rejected(self):
        now = timezone.now()
        self._code(valid_from=now - timedelta(days=10), valid_until=now - timedelta(days=1))
        with self.assertRaises(DiscountError):
            quote_discount("SAVE20", self.school, Decimal("100"))
        DiscountCode.objects.all().delete()
        self._code(valid_from=now + timedelta(days=1))
        with self.assertRaises(DiscountError):
            quote_discount("SAVE20", self.school, Decimal("100"))

    def test_exhausted_code_is_rejected(self):
        code = self._code(max_uses=1)
        DiscountRedemption.objects.create(
            discount_code=code,
            school=self.other_school,
            amount_discounted=Decimal("10.00"),
        )
        with self.assertRaises(DiscountError):
            quote_discount("SAVE20", self.school, Decimal("100"))

    def test_school_cannot_reuse_code(self):
        code = self._code()
        DiscountRedemption.objects.create(
            discount_code=code,
            school=self.school,
            amount_discounted=Decimal("10.00"),
        )
        with self.assertRaises(DiscountError):
            quote_discount("SAVE20", self.school, Decimal("100"))
        # مدرسة أخرى ما زالت تستطيع الاستخدام
        q = quote_discount("SAVE20", self.other_school, Decimal("100.00"))
        self.assertEqual(q.discount_amount, Decimal("20.00"))


class DiscountRedemptionTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة التسجيل", slug="redeem-school")
        self.plan = SubscriptionPlan.objects.create(
            code="basic-redeem",
            name="أساسية",
            price=Decimal("500.00"),
            duration_days=365,
            max_screens=3,
        )
        self.code = DiscountCode.objects.create(
            code="ONCE",
            discount_type=DiscountCode.TYPE_AMOUNT,
            amount=Decimal("50.00"),
            max_uses=2,
        )

    def _checkout(self):
        return MoyasarCheckout.objects.create(
            school=self.school,
            plan=self.plan,
            request_type="new",
            amount=Decimal("450.00"),
            discount_code=self.code,
            discount_amount=Decimal("50.00"),
        )

    def test_redemption_recorded_once_per_checkout(self):
        checkout = self._checkout()
        first = record_redemption(checkout)
        second = record_redemption(checkout)
        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.code.used_count, 1)
        self.assertEqual(self.code.remaining_uses, 1)

    def test_checkout_without_code_records_nothing(self):
        checkout = MoyasarCheckout.objects.create(
            school=self.school,
            plan=self.plan,
            request_type="new",
            amount=Decimal("500.00"),
        )
        self.assertIsNone(record_redemption(checkout))
        self.assertEqual(DiscountRedemption.objects.count(), 0)

    def test_code_becomes_unusable_after_exhaustion(self):
        checkout = self._checkout()
        record_redemption(checkout)
        other = School.objects.create(name="مدرسة ثانية", slug="second-school")
        DiscountRedemption.objects.create(
            discount_code=self.code,
            school=other,
            amount_discounted=Decimal("50.00"),
        )
        self.assertFalse(self.code.is_usable())


from django.contrib.auth import get_user_model  # noqa: E402
from django.test import override_settings  # noqa: E402
from django.urls import reverse  # noqa: E402

from core.models import UserProfile  # noqa: E402


@override_settings(
    DEBUG=True,
    MOYASAR_ENABLED=True,
    MOYASAR_LIVE_MODE=False,
    MOYASAR_ACTIVATE_TEST_PAYMENTS=False,
    MOYASAR_PUBLISHABLE_KEY="pk_test_publishable",
    MOYASAR_SECRET_KEY="sk_test_secret",
    MOYASAR_WEBHOOK_SECRET="webhook-test-secret",
    MOYASAR_CALLBACK_BASE_URL="https://school-display.com",
)
class DiscountCheckoutFlowTests(TestCase):
    """اختبار المسار الكامل: إدخال الكود في بدء الدفع وخصمه من الإجمالي."""

    def setUp(self):
        self.school = School.objects.create(name="مدرسة التدفق", slug="flow-school")
        self.plan = SubscriptionPlan.objects.create(
            code="flow-plan",
            name="سنوية",
            price=Decimal("1000.00"),
            duration_days=365,
            max_screens=3,
        )
        self.manager = get_user_model().objects.create_user(
            username="flow_manager",
            password="StrongPass123!",
            email="flow@example.com",
        )
        profile = UserProfile.objects.create(
            user=self.manager,
            active_school=self.school,
            email_verified_at=timezone.now(),
        )
        profile.schools.add(self.school)
        self.code = DiscountCode.objects.create(
            code="FLOW25",
            discount_type=DiscountCode.TYPE_PERCENT,
            percent=Decimal("25"),
            max_uses=10,
        )

    def _start(self, discount_code=""):
        self.client.force_login(self.manager)
        payload = {"request_type": "new", "plan_id": self.plan.pk}
        if discount_code:
            payload["discount_code"] = discount_code
        return self.client.post(reverse("subscriptions:moyasar_start"), payload)

    def test_valid_code_reduces_checkout_amount(self):
        response = self._start("flow25")

        checkout = MoyasarCheckout.objects.get()
        self.assertEqual(checkout.amount, Decimal("750.00"))
        self.assertEqual(checkout.discount_amount, Decimal("250.00"))
        self.assertEqual(checkout.discount_code_id, self.code.pk)
        self.assertRedirects(
            response,
            reverse(
                "subscriptions:moyasar_checkout",
                kwargs={"reference": checkout.merchant_reference},
            ),
        )
        page = self.client.get(response.url)
        self.assertContains(page, "FLOW25")
        self.assertContains(page, "بعد الخصم")

    def test_invalid_code_blocks_checkout_creation(self):
        response = self._start("WRONG")

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertFalse(MoyasarCheckout.objects.exists())

    def test_checkout_without_code_charges_full_price(self):
        self._start()

        checkout = MoyasarCheckout.objects.get()
        self.assertEqual(checkout.amount, Decimal("1000.00"))
        self.assertEqual(checkout.discount_amount, Decimal("0.00"))
        self.assertIsNone(checkout.discount_code_id)

    def test_full_discount_below_minimum_is_rejected(self):
        DiscountCode.objects.create(
            code="FREE100",
            discount_type=DiscountCode.TYPE_PERCENT,
            percent=Decimal("100"),
            max_uses=5,
        )
        response = self._start("FREE100")

        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertFalse(MoyasarCheckout.objects.exists())


class DiscountPlanRestrictionTests(TestCase):
    """قصر الكود على باقات معينة: فارغ = كل الباقات، محدد = المذكورة فقط."""

    def setUp(self):
        self.school = School.objects.create(name="مدرسة الباقات", slug="plans-school")
        self.basic = SubscriptionPlan.objects.create(
            code="basic-r", name="أساسية", price=Decimal("500.00"),
            duration_days=365, max_screens=3,
        )
        self.premium = SubscriptionPlan.objects.create(
            code="premium-r", name="مميزة", price=Decimal("900.00"),
            duration_days=365, max_screens=10,
        )
        self.code = DiscountCode.objects.create(
            code="PREMONLY",
            discount_type=DiscountCode.TYPE_PERCENT,
            percent=Decimal("10"),
            max_uses=5,
        )
        self.code.plans.add(self.premium)

    def test_code_without_plans_allows_any_plan(self):
        open_code = DiscountCode.objects.create(
            code="OPEN",
            discount_type=DiscountCode.TYPE_PERCENT,
            percent=Decimal("10"),
            max_uses=5,
        )
        for plan in (self.basic, self.premium):
            q = quote_discount("OPEN", self.school, Decimal("100.00"), plan=plan)
            self.assertEqual(q.discount_amount, Decimal("10.00"))
        self.assertTrue(open_code.allows_plan(self.basic))

    def test_restricted_code_accepts_listed_plan_only(self):
        q = quote_discount("PREMONLY", self.school, Decimal("900.00"), plan=self.premium)
        self.assertEqual(q.discount_amount, Decimal("90.00"))

        with self.assertRaises(DiscountError):
            quote_discount("PREMONLY", self.school, Decimal("500.00"), plan=self.basic)

    def test_quote_without_plan_context_ignores_restriction(self):
        # التحقق بلا سياق باقة (مثلاً من أدوات إدارية) لا يفشل بسبب القصر.
        q = quote_discount("PREMONLY", self.school, Decimal("100.00"))
        self.assertEqual(q.discount_amount, Decimal("10.00"))


@override_settings(
    DEBUG=True,
    MOYASAR_ENABLED=True,
    MOYASAR_LIVE_MODE=False,
    MOYASAR_ACTIVATE_TEST_PAYMENTS=False,
    MOYASAR_PUBLISHABLE_KEY="pk_test_publishable",
    MOYASAR_SECRET_KEY="sk_test_secret",
    MOYASAR_WEBHOOK_SECRET="webhook-test-secret",
    MOYASAR_CALLBACK_BASE_URL="https://school-display.com",
)
class DiscountPlanRestrictionCheckoutTests(TestCase):
    """المسار الكامل: كود مقصور على باقة يُرفض عند الدفع لباقة أخرى."""

    def setUp(self):
        self.school = School.objects.create(name="مدرسة قصر الدفع", slug="restrict-flow")
        self.basic = SubscriptionPlan.objects.create(
            code="basic-cf", name="أساسية", price=Decimal("500.00"),
            duration_days=365, max_screens=3,
        )
        self.premium = SubscriptionPlan.objects.create(
            code="premium-cf", name="مميزة", price=Decimal("1000.00"),
            duration_days=365, max_screens=10,
        )
        self.manager = get_user_model().objects.create_user(
            username="restrict_manager",
            password="StrongPass123!",
            email="restrict@example.com",
        )
        profile = UserProfile.objects.create(
            user=self.manager,
            active_school=self.school,
            email_verified_at=timezone.now(),
        )
        profile.schools.add(self.school)
        code = DiscountCode.objects.create(
            code="PREMFLOW",
            discount_type=DiscountCode.TYPE_PERCENT,
            percent=Decimal("20"),
            max_uses=5,
        )
        code.plans.add(self.premium)

    def _start(self, plan, discount_code):
        self.client.force_login(self.manager)
        return self.client.post(
            reverse("subscriptions:moyasar_start"),
            {"request_type": "new", "plan_id": plan.pk, "discount_code": discount_code},
        )

    def test_restricted_code_works_for_its_plan(self):
        self._start(self.premium, "PREMFLOW")
        checkout = MoyasarCheckout.objects.get()
        self.assertEqual(checkout.amount, Decimal("800.00"))
        self.assertEqual(checkout.discount_amount, Decimal("200.00"))

    def test_restricted_code_is_rejected_for_other_plans(self):
        response = self._start(self.basic, "PREMFLOW")
        self.assertRedirects(response, reverse("dashboard:my_subscription"))
        self.assertFalse(MoyasarCheckout.objects.exists())

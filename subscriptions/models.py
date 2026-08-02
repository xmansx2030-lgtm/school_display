from datetime import date
from decimal import Decimal
from datetime import timedelta
import os
import uuid

from django.conf import settings
import secrets

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import School, SubscriptionPlan


_RECEIPT_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}


def _validate_receipt_extension(value):
    if not value:
        return
    name = (getattr(value, "name", "") or "").strip()
    _, ext = os.path.splitext(name)
    ext = (ext or "").lower()
    if ext == "":
        return
    if ext not in _RECEIPT_ALLOWED_EXTS:
        raise ValidationError(
            f"امتداد الملف \"{ext.lstrip('.')}\" غير مسموح به. "
            f"الامتدادات المسموح بها: {', '.join(sorted(e.lstrip('.') for e in _RECEIPT_ALLOWED_EXTS))}.",
            code="invalid_extension",
        )


def _tamara_reference() -> str:
    return f"SD-{uuid.uuid4().hex}"


class SchoolSubscription(models.Model):
    STATUS_CHOICES = [
        ("pending", "قيد الإعداد"),
        ("active", "سارية"),
        ("expired", "منتهية"),
        ("cancelled", "ملغاة"),
    ]
    CLOSURE_REASON_CHOICES = [
        ("budget", "الميزانية"),
        ("not_used", "ضعف الاستخدام"),
        ("technical", "مشكلة تقنية"),
        ("competitor", "الانتقال إلى منافس"),
        ("school_closed", "إغلاق أو دمج المدرسة"),
        ("no_response", "تعذر التواصل"),
        ("other", "سبب آخر"),
    ]

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        verbose_name="المدرسة",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="subscriptions",
        verbose_name="الخطة",
    )
    starts_at = models.DateField(
        default=timezone.localdate,
        verbose_name="بداية الاشتراك",
    )
    ends_at = models.DateField(
        null=True,
        blank=True,
        verbose_name="نهاية الاشتراك",
        help_text="اتركه فارغًا ليكون مفتوح المدة.",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="active",
        verbose_name="الحالة",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="ملاحظات",
    )
    closure_reason = models.CharField(
        "سبب الإلغاء أو عدم التجديد",
        max_length=30,
        choices=CLOSURE_REASON_CHOICES,
        blank=True,
    )
    closure_notes = models.TextField(
        "تفاصيل سبب الإلغاء أو عدم التجديد",
        blank=True,
    )
    closed_at = models.DateTimeField(
        "وقت تسجيل الإغلاق",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="تاريخ الإنشاء",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="آخر تحديث",
    )

    class Meta:
        verbose_name = "اشتراك مدرسة"
        verbose_name_plural = "اشتراكات المدارس"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["school", "plan", "starts_at"],
                name="uniq_school_plan_start",
            )
        ]

    def __str__(self) -> str:
        return f"{self.school} - {self.plan} ({self.starts_at})"

    def save(self, *args, **kwargs):
        # إذا لم يُحدد تاريخ نهاية يدويًا، احسبه تلقائيًا من مدة الباقة.
        if not self.ends_at and self.starts_at and getattr(self, "plan", None):
            try:
                days = getattr(self.plan, "duration_days", None)
                if days is not None:
                    days_int = int(days)
                    if days_int > 0:
                        # حسب المطلوب: النهاية = تاريخ البداية + عدد الأيام
                        self.ends_at = self.starts_at + timedelta(days=days_int)
            except Exception:
                pass

        if self.status in {"cancelled", "expired"} and not self.closed_at:
            self.closed_at = timezone.now()
        elif self.status not in {"cancelled", "expired"}:
            self.closed_at = None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"closed_at"}

        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        """
        هل الاشتراك ساري المفعول حاليًا؟
        يعتمد على الحالة والتاريخ.
        """
        today = timezone.localdate()

        if self.status != "active":
            return False

        if self.ends_at and self.ends_at < today:
            return False

        return True

    @property
    def days_left(self):
        """
        عدد الأيام المتبقية حتى انتهاء الاشتراك.
        يرجع None إذا كان الاشتراك مفتوح المدة (بدون تاريخ انتهاء).
        """
        if not self.ends_at:
            return None  # اشتراك مفتوح المدة

        today = date.today()
        delta = (self.ends_at - today).days
        return delta if delta >= 0 else 0


class SubscriptionScreenAddon(models.Model):
    """شراء/إضافة عدد شاشات فوق حد الباقة لمدّة اشتراك معيّنة."""

    STATUS_CHOICES = [
        ("pending", "بانتظار الدفع"),
        ("paid", "مدفوع"),
        ("cancelled", "ملغي"),
        ("refunded", "مُسترد"),
    ]

    PRICING_STRATEGY_CHOICES = [
        ("auto_bundle", "تلقائي (شرائح)"),
        ("manual_bundle", "يدوي (مبلغ إجمالي)"),
        ("manual_per_screen", "يدوي (سعر لكل شاشة)"),
    ]

    PRICING_CYCLE_CHOICES = [
        ("inherit", "حسب الاشتراك"),
        ("monthly", "شهري"),
        ("semiannual", "نصف سنوي"),
        ("annual", "سنوي"),
    ]

    subscription = models.ForeignKey(
        SchoolSubscription,
        on_delete=models.CASCADE,
        related_name="screen_addons",
        verbose_name="الاشتراك",
    )
    screens_added = models.PositiveIntegerField(
        verbose_name="عدد الشاشات المضافة",
        help_text="عدد الشاشات الإضافية المطلوبة لهذه الفترة.",
    )

    pricing_strategy = models.CharField(
        max_length=30,
        choices=PRICING_STRATEGY_CHOICES,
        default="auto_bundle",
        verbose_name="طريقة التسعير",
        help_text="التلقائي: 1=25،2=40،3=55 وفوق 3 +15 لكل شاشة. السنوي×10، نصف سنوي×5.",
    )

    bundle_price = models.DecimalField(
        "سعر الإضافة للفترة (ر.س)",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="يُستخدم مع (يدوي مبلغ إجمالي) أو يتم تعبئته تلقائيًا في (تلقائي شرائح).",
    )

    unit_price = models.DecimalField(
        "سعر للشاشة (ر.س)",
        max_digits=8,
        decimal_places=2,
        default=0,
    )
    proration_factor = models.DecimalField(
        "معامل الاحتساب النسبي",
        max_digits=6,
        decimal_places=5,
        default=Decimal("1.0"),
        help_text="1.0 = كامل المدة. يتم حسابه تلقائيًا عند الحفظ إذا كان للمدة نهاية.",
    )
    total_price = models.DecimalField(
        "الإجمالي (ر.س)",
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="يتم حسابه تلقائيًا: screens_added × unit_price × proration_factor.",
    )
    starts_at = models.DateField(
        default=timezone.localdate,
        verbose_name="بداية الإضافة",
    )

    validity_days = models.PositiveIntegerField(
        verbose_name="مدة الصلاحية بالأيام",
        null=True,
        blank=True,
        help_text="اختياري: لو حددتها ستصبح نهاية الإضافة = البداية + (المدة-1). مثال شهر: 30 يوم.",
    )

    pricing_cycle = models.CharField(
        max_length=20,
        choices=PRICING_CYCLE_CHOICES,
        default="inherit",
        verbose_name="دورة تسعير الإضافة",
        help_text="مثال: اشتراك سنوي لكن إضافة لمدة شهر → اختر (شهري) وحدد مدة 30 يوم.",
    )
    ends_at = models.DateField(
        null=True,
        blank=True,
        verbose_name="نهاية الإضافة",
        help_text="افتراضيًا تُضبط على نهاية الاشتراك إن وُجدت.",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="paid",
        verbose_name="الحالة",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="ملاحظات",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="تاريخ الإنشاء",
    )

    class Meta:
        verbose_name = "زيادة شاشات"
        verbose_name_plural = "زيادات الشاشات"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.subscription.school} +{self.screens_added} شاشة"

    @property
    def is_effective_today(self) -> bool:
        today = timezone.localdate()
        if self.status != "paid":
            return False
        if self.starts_at and self.starts_at > today:
            return False
        if self.ends_at and self.ends_at < today:
            return False
        return True

    def _calc_proration_factor(self, as_of=None) -> Decimal:
        """احتساب نسبي بناءً على نافذة صلاحية الإضافة (starts_at→ends_at).

        - إذا لم يكن هناك ends_at → 1.0
        - يتم تثبيت الحساب على تاريخ الشراء/البداية (as_of)، وليس "اليوم"، لتجنب تغيّر الفاتورة لاحقًا.
        """
        as_of_date = as_of or self.starts_at or timezone.localdate()

        if not self.ends_at or not self.starts_at:
            return Decimal("1.0")

        period_days = (self.ends_at - self.starts_at).days + 1
        if period_days <= 0:
            return Decimal("1.0")

        remaining_days = (self.ends_at - as_of_date).days + 1
        if remaining_days <= 0:
            return Decimal("0")

        factor = (Decimal(remaining_days) / Decimal(period_days))
        if factor < 0:
            return Decimal("0")
        if factor > 1:
            return Decimal("1")
        return factor

    def _infer_subscription_cycle_multiplier(self) -> Decimal:
        """استنتاج معامل مدة الاشتراك من طول الاشتراك (fallback عند اختيار inherit)."""
        sub_start = getattr(self.subscription, "starts_at", None)
        sub_end = getattr(self.subscription, "ends_at", None)
        if not sub_start or not sub_end:
            return Decimal("1")

        days = (sub_end - sub_start).days + 1
        # حدود مرنة لتغطية اختلاف الأشهر/السنة
        if days >= 330:
            return Decimal("10")
        if days >= 150:
            return Decimal("5")
        return Decimal("1")

    def _cycle_multiplier(self) -> Decimal:
        """معامل دورة تسعير الإضافة."""
        c = (self.pricing_cycle or "inherit").strip().lower()
        if c == "annual":
            return Decimal("10")
        if c == "semiannual":
            return Decimal("5")
        if c == "monthly":
            return Decimal("1")
        return self._infer_subscription_cycle_multiplier()

    def _calc_auto_monthly_bundle_price(self) -> Decimal:
        """تسعير الشرائح الشهري حسب العدد.

        1=25، 2=40، 3=55، وفوق 3: 55 + (n-3)*15
        """
        n = int(self.screens_added or 0)
        if n <= 0:
            return Decimal("0")
        if n == 1:
            return Decimal("25")
        if n == 2:
            return Decimal("40")
        if n == 3:
            return Decimal("55")
        return Decimal("55") + (Decimal(n - 3) * Decimal("15"))

    def _calc_auto_bundle_price_for_cycle(self) -> Decimal:
        monthly = self._calc_auto_monthly_bundle_price()
        return (monthly * self._cycle_multiplier()).quantize(Decimal("0.01"))

    def _default_ends_at_for_cycle(self):
        """يضبط ends_at تلقائيًا عند عدم تحديدها.

        - لو validity_days موجودة → تعتمد عليها
        - لو دورة الإضافة محددة (شهري/نصف سنوي/سنوي) → مدة ثابتة تقريبية
        - وإلا → توريث نهاية الاشتراك إن وُجدت
        """
        if self.starts_at and self.validity_days:
            try:
                days = int(self.validity_days)
                if days > 0:
                    return self.starts_at + timedelta(days=days - 1)
            except Exception:
                pass

        c = (self.pricing_cycle or "inherit").strip().lower()
        if self.starts_at:
            if c == "monthly":
                return self.starts_at + timedelta(days=30 - 1)
            if c == "semiannual":
                return self.starts_at + timedelta(days=182 - 1)
            if c == "annual":
                return self.starts_at + timedelta(days=365 - 1)

        try:
            return getattr(self.subscription, "ends_at", None)
        except Exception:
            return None

    def _calc_total_price(self) -> Decimal:
        factor = Decimal(str(self.proration_factor or 0))

        if self.pricing_strategy == "manual_per_screen":
            unit = Decimal(str(self.unit_price or 0))
            qty = Decimal(int(self.screens_added or 0))
            total = unit * qty * factor
            return total.quantize(Decimal("0.01"))

        # bundle strategies
        bundle = Decimal(str(self.bundle_price or 0))
        total = bundle * factor
        return total.quantize(Decimal("0.01"))

    def save(self, *args, **kwargs):
        # default ends_at
        if self.ends_at is None:
            self.ends_at = self._default_ends_at_for_cycle()

        # compute proration (freeze to starts_at)
        self.proration_factor = self._calc_proration_factor(as_of=self.starts_at)

        # auto pricing (bundle) if selected
        if self.pricing_strategy == "auto_bundle":
            self.bundle_price = self._calc_auto_bundle_price_for_cycle()

        # compute total
        self.total_price = self._calc_total_price()

        super().save(*args, **kwargs)


class SubscriptionRequest(models.Model):
    REQUEST_TYPE_CHOICES = [
        ("renewal", "طلب تجديد"),
        ("new", "طلب اشتراك جديد"),
    ]

    STATUS_CHOICES = [
        ("submitted", "مرسلة"),
        ("under_review", "قيد المراجعة"),
        ("approved", "معتمدة"),
        ("rejected", "مرفوضة"),
    ]

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="subscription_requests",
        verbose_name="المدرسة",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_subscription_requests",
        verbose_name="أُنشئ بواسطة",
    )

    request_type = models.CharField(
        max_length=20,
        choices=REQUEST_TYPE_CHOICES,
        verbose_name="نوع الطلب",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="subscription_requests",
        verbose_name="الخطة المطلوبة",
    )

    # حسب طلبك: يبدأ التجديد من تاريخ رفع الطلب (يُثبت عند الإرسال)
    requested_starts_at = models.DateField(
        default=timezone.localdate,
        verbose_name="تاريخ بدء التفعيل المطلوب",
    )

    amount = models.DecimalField(
        "مبلغ التحويل (ر.س)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    receipt_image = models.ImageField(
        upload_to="receipts/subscription_requests/%Y/%m",
        max_length=500,
        blank=True,
        verbose_name="إيصال التحويل (صورة)",
        validators=[_validate_receipt_extension],
    )
    transfer_note = models.CharField(
        "ملاحظة العميل",
        max_length=255,
        blank=True,
        default="",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="submitted",
        verbose_name="الحالة",
    )
    admin_note = models.TextField(
        "ملاحظة الإدارة",
        blank=True,
        default="",
    )

    processed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_subscription_requests",
        verbose_name="تمت المراجعة بواسطة",
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="تاريخ المراجعة",
    )
    approved_subscription = models.ForeignKey(
        "subscriptions.SchoolSubscription",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_from_requests",
        verbose_name="الاشتراك المُنشأ",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="آخر تحديث")

    class Meta:
        verbose_name = "طلب اشتراك/تجديد"
        verbose_name_plural = "طلبات الاشتراك/التجديد"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.school} - {self.get_request_type_display()} ({self.get_status_display()})"


class SubscriptionPaymentOperation(models.Model):
    """سجل عمليات الدفع/التفعيل للاشتراكات.

    الهدف: عند إنشاء اشتراك يدويًا من لوحة النظام، نسجل طريقة الدفع (تحويل/رابط/تمارا)
    حتى يظهر لدينا تاريخ واضح لآلية الدفع.
    """

    METHOD_CHOICES = [
        ("bank_transfer", "تحويل"),
        ("payment_link", "رابط دفع"),
        ("tamara", "تمارا"),
    ]

    SOURCE_CHOICES = [
        ("admin_manual", "إضافة يدوية"),
        ("request", "طلب اشتراك"),
    ]

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="subscription_payment_operations",
        verbose_name="المدرسة",
    )
    subscription = models.ForeignKey(
        SchoolSubscription,
        on_delete=models.CASCADE,
        related_name="payment_operations",
        verbose_name="الاشتراك",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="payment_operations",
        verbose_name="الخطة",
    )

    amount = models.DecimalField(
        "المبلغ (ر.س)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    method = models.CharField(
        "طريقة الدفع",
        max_length=20,
        choices=METHOD_CHOICES,
    )
    source = models.CharField(
        "مصدر العملية",
        max_length=20,
        choices=SOURCE_CHOICES,
        default="admin_manual",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_subscription_payment_operations",
        verbose_name="تمت بواسطة",
    )
    note = models.CharField(
        "ملاحظة",
        max_length=255,
        blank=True,
        default="",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ العملية")

    class Meta:
        verbose_name = "عملية دفع اشتراك"
        verbose_name_plural = "عمليات دفع الاشتراكات"
        ordering = ("-created_at", "-id")

    def __str__(self) -> str:
        return f"{self.school} - {self.plan} - {self.get_method_display()}"


class TamaraCheckout(models.Model):
    REQUEST_TYPE_CHOICES = SubscriptionRequest.REQUEST_TYPE_CHOICES
    STATUS_CHOICES = [
        ("initiated", "بدأت"),
        ("new", "بانتظار الدفع"),
        ("approved", "تمت الموافقة"),
        ("authorised", "مصرح بها"),
        ("captured", "مكتملة"),
        ("declined", "مرفوضة"),
        ("canceled", "ملغاة"),
        ("expired", "منتهية"),
        ("refunded", "مستردة"),
        ("error", "خطأ"),
    ]

    merchant_reference = models.CharField(
        "مرجع التاجر",
        max_length=40,
        unique=True,
        default=_tamara_reference,
        editable=False,
    )
    tamara_order_id = models.CharField(
        "رقم طلب تمارا",
        max_length=80,
        unique=True,
        null=True,
        blank=True,
    )
    checkout_id = models.CharField("رقم جلسة الدفع", max_length=80, blank=True, default="")
    checkout_url = models.URLField("رابط الدفع", max_length=2048, blank=True, default="")
    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="tamara_checkouts",
        verbose_name="المدرسة",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tamara_checkouts",
        verbose_name="أنشئت بواسطة",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="tamara_checkouts",
        verbose_name="الخطة",
    )
    request_type = models.CharField(max_length=20, choices=REQUEST_TYPE_CHOICES)
    starts_at = models.DateField("بداية الاشتراك المطلوبة", default=timezone.localdate)
    amount = models.DecimalField("المبلغ", max_digits=10, decimal_places=2)
    currency = models.CharField("العملة", max_length=3, default="SAR")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="initiated")
    last_event = models.CharField("آخر حدث", max_length=40, blank=True, default="")
    error_message = models.CharField("رسالة الخطأ", max_length=300, blank=True, default="")
    subscription = models.ForeignKey(
        SchoolSubscription,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tamara_checkouts",
    )
    payment_operation = models.OneToOneField(
        SubscriptionPaymentOperation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tamara_checkout",
    )
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "جلسة دفع تمارا"
        verbose_name_plural = "جلسات دفع تمارا"
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["school", "status", "created_at"], name="tamara_school_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.merchant_reference} - {self.get_status_display()}"

class SubscriptionInvoice(models.Model):
    """فاتورة إلكترونية محفوظة (HTML snapshot) مرتبطة بعملية دفع اشتراك."""

    CURRENCY_CHOICES = [
        ("SAR", "ر.س"),
    ]

    operation = models.OneToOneField(
        SubscriptionPaymentOperation,
        on_delete=models.PROTECT,
        related_name="invoice",
        verbose_name="عملية الدفع",
    )

    school = models.ForeignKey(
        School,
        on_delete=models.PROTECT,
        related_name="subscription_invoices",
        verbose_name="المدرسة",
    )
    subscription = models.ForeignKey(
        SchoolSubscription,
        on_delete=models.PROTECT,
        related_name="invoices",
        verbose_name="الاشتراك",
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        related_name="subscription_invoices",
        verbose_name="الخطة",
    )

    invoice_number = models.CharField(
        "رقم الفاتورة",
        max_length=40,
        unique=True,
        editable=False,
    )
    issued_at = models.DateTimeField(
        "تاريخ الإصدار",
        default=timezone.now,
    )

    currency = models.CharField(
        "العملة",
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="SAR",
    )
    amount = models.DecimalField(
        "المبلغ (ر.س)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    payment_method = models.CharField(
        "طريقة الدفع",
        max_length=20,
        choices=SubscriptionPaymentOperation.METHOD_CHOICES,
    )

    seller_name = models.CharField(
        "الطرف الأول (المنصة)",
        max_length=200,
        blank=True,
        default="",
    )
    buyer_name = models.CharField(
        "الطرف الثاني (المدرسة)",
        max_length=200,
        blank=True,
        default="",
    )

    html_snapshot = models.TextField(
        "نسخة HTML",
        blank=True,
        default="",
        help_text="يُحفظ HTML النهائي للفاتورة لضمان ثباتها حتى لو تغيّر تصميم القالب لاحقًا.",
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="تاريخ الإنشاء")

    class Meta:
        verbose_name = "فاتورة اشتراك"
        verbose_name_plural = "فواتير الاشتراكات"
        ordering = ("-issued_at", "-id")

    def __str__(self) -> str:
        return self.invoice_number

    @staticmethod
    def _make_invoice_number() -> str:
        # رقم فريد وبسيط للقراءة (بدون الاعتماد على تسلسل DB)
        token = secrets.token_hex(4).upper()
        stamp = timezone.now().strftime("%Y%m%d")
        return f"INV-{stamp}-{token}"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            # حاول عدة مرات لضمان uniqueness
            for _ in range(10):
                cand = self._make_invoice_number()
                if not SubscriptionInvoice.objects.filter(invoice_number=cand).exclude(pk=self.pk).exists():
                    self.invoice_number = cand
                    break
        super().save(*args, **kwargs)


class SubscriptionEmailNotification(models.Model):
    """Durable outbox for customer invoice and subscription email messages."""

    class EventType(models.TextChoices):
        INVOICE = "invoice", "فاتورة اشتراك"
        EXPIRY = "expiry", "تنبيه قرب انتهاء الاشتراك"

    class Status(models.TextChoices):
        PENDING = "pending", "بانتظار الإرسال"
        PROCESSING = "processing", "جارٍ الإرسال"
        SENT = "sent", "تم الإرسال"
        FAILED = "failed", "فشل نهائي"

    event_type = models.CharField(max_length=20, choices=EventType.choices)
    subscription = models.ForeignKey(
        SchoolSubscription,
        on_delete=models.CASCADE,
        related_name="email_notifications",
    )
    invoice = models.ForeignKey(
        SubscriptionInvoice,
        on_delete=models.CASCADE,
        related_name="email_notifications",
        null=True,
        blank=True,
    )
    recipient = models.EmailField(max_length=254)
    reminder_days = models.PositiveSmallIntegerField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [
            models.Index(
                fields=("status", "available_at"),
                name="sub_email_status_available_idx",
            )
        ]
        verbose_name = "إشعار بريد اشتراك"
        verbose_name_plural = "إشعارات بريد الاشتراكات"

    def __str__(self) -> str:
        return f"{self.get_event_type_display()} - {self.recipient}"

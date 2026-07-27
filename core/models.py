from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
import os
import secrets


_IMAGE_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _validate_image_extension(value):
    if not value:
        return
    name = (getattr(value, "name", "") or "").strip()
    _, ext = os.path.splitext(name)
    ext = (ext or "").lower()
    if ext == "":
        return
    if ext not in _IMAGE_ALLOWED_EXTS:
        raise ValidationError(
            f"امتداد الملف \"{ext.lstrip('.')}\" غير مسموح به. "
            f"الامتدادات المسموح بها: {', '.join(sorted(e.lstrip('.') for e in _IMAGE_ALLOWED_EXTS))}.",
            code="invalid_extension",
        )


class SchoolType(models.TextChoices):
    BOYS = "boys", "بنين"
    GIRLS = "girls", "بنات"


class School(models.Model):
    name = models.CharField("اسم المدرسة", max_length=150)
    slug = models.SlugField(
        "رابط المدرسة",
        unique=True,
        help_text="يستخدم في الرابط، مثلا: school-1",
    )
    logo = models.ImageField(
        "الشعار",
        upload_to="schools/logos/",
        blank=True,
        null=True,
        validators=[_validate_image_extension],
    )
    is_active = models.BooleanField(
        "اشتراك نشط",
        default=True,
    )

    school_type = models.CharField(
        "نوع المدرسة",
        max_length=10,
        choices=SchoolType.choices,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "مدرسة"
        verbose_name_plural = "المدارس"

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name="المستخدم",
    )
    schools = models.ManyToManyField(
        School,
        related_name="users",
        verbose_name="المدارس المتاحة",
        blank=True,
    )
    active_school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        related_name="active_users",
        verbose_name="المدرسة النشطة",
        null=True,
        blank=True,
    )
    mobile = models.CharField(
        "رقم الجوال",
        max_length=20,
        blank=True,
        null=True,
    )
    needs_onboarding = models.BooleanField(
        "يحتاج إلى دليل البدء",
        default=False,
        help_text="يوجّه المستخدم الجديد إلى دليل البدء مرة واحدة بعد أول دخول.",
    )

    class Meta:
        verbose_name = "ملف المستخدم"
        verbose_name_plural = "ملفات المستخدمين"

    def __str__(self):
        return f"{self.user.username} - {self.active_school.name if self.active_school else 'No Active School'}"


class UserTwoFactorAuth(models.Model):
    """Encrypted TOTP configuration for privileged system accounts."""

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="two_factor_auth",
        verbose_name="المستخدم",
    )
    encrypted_secret = models.TextField("سر المصادقة المشفر", blank=True)
    is_enabled = models.BooleanField("مفعلة", default=False)
    recovery_code_hashes = models.JSONField("رموز الاسترداد المشفرة", default=list, blank=True)
    last_used_counter = models.BigIntegerField("آخر عداد مستخدم", null=True, blank=True)
    confirmed_at = models.DateTimeField("تاريخ التفعيل", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "مصادقة ثنائية"
        verbose_name_plural = "المصادقة الثنائية"

    def __str__(self) -> str:
        return f"2FA: {self.user} ({'enabled' if self.is_enabled else 'pending'})"


class DisplayScreen(models.Model):
    short_code = models.CharField(
        max_length=8,
        unique=True,
        editable=False,
        verbose_name="رابط مختصر",
        null=True,
        blank=True,
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="screens",
        verbose_name="المدرسة",
        null=True,
        blank=True,
    )
    name = models.CharField(
        max_length=100,
        verbose_name="اسم الشاشة",
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        verbose_name="رمز الوصول",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="نشط",
    )
    auto_disabled_by_limit = models.BooleanField(
        default=False,
        verbose_name="موقوف تلقائياً بسبب الحد",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="آخر ظهور",
    )

    # ربط الشاشة بجهاز واحد (TV) لمنع مشاركة الرابط
    bound_device_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name="معرّف الجهاز المرتبط",
    )
    bound_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="تاريخ ربط الجهاز",
    )

    class Meta:
        verbose_name = "شاشة عرض"
        verbose_name_plural = "شاشات العرض"

    @staticmethod
    def _bad_value(v) -> bool:
        v = (v or "").strip()
        return (not v) or (v == "-") or (v.lower() == "none")

    def save(self, *args, **kwargs):
        # ✅ توليد token حتى لو كان None أو "" أو " - "
        if self._bad_value(self.token):
            # 64 hex chars
            for _ in range(30):
                t = secrets.token_hex(32)
                if not DisplayScreen.objects.filter(token=t).exclude(pk=self.pk).exists():
                    self.token = t
                    break

        # ✅ توليد short_code سهل للكتابة على TV (بدون 0/O و 1/I و l)
        if self._bad_value(self.short_code):
            alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
            for _ in range(80):
                code = "".join(secrets.choice(alphabet) for _ in range(6))
                if not DisplayScreen.objects.filter(short_code__iexact=code).exclude(pk=self.pk).exists():
                    self.short_code = code
                    break

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.school.name} - {self.name}" if self.school else self.name


class ScreenOutage(models.Model):
    screen = models.ForeignKey(
        DisplayScreen,
        on_delete=models.CASCADE,
        related_name="outages",
        verbose_name="الشاشة",
    )
    detected_at = models.DateTimeField("وقت اكتشاف التعطل", default=timezone.now)
    last_seen_at = models.DateTimeField("آخر ظهور قبل التعطل", null=True, blank=True)
    alert_sent_at = models.DateTimeField("وقت إرسال التنبيه", null=True, blank=True)
    resolved_at = models.DateTimeField("وقت عودة الاتصال", null=True, blank=True)

    class Meta:
        verbose_name = "تعطل شاشة"
        verbose_name_plural = "أعطال الشاشات"
        ordering = ("-detected_at",)
        indexes = [
            models.Index(fields=("screen", "resolved_at"), name="screen_outage_open_idx"),
            models.Index(fields=("detected_at",), name="screen_outage_detect_idx"),
        ]

    def __str__(self):
        return f"{self.screen} — {self.detected_at:%Y-%m-%d %H:%M}"


class ScreenWeeklyUptimeReport(models.Model):
    screen = models.ForeignKey(
        DisplayScreen,
        on_delete=models.CASCADE,
        related_name="weekly_uptime_reports",
        verbose_name="الشاشة",
    )
    week_start = models.DateField("بداية الأسبوع")
    week_end = models.DateField("نهاية الأسبوع")
    uptime_percent = models.DecimalField(
        "نسبة التشغيل",
        max_digits=5,
        decimal_places=2,
        default=100,
    )
    offline_seconds = models.PositiveBigIntegerField("ثواني الانقطاع", default=0)
    sent_at = models.DateTimeField("وقت إرسال التقرير", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تقرير تشغيل أسبوعي"
        verbose_name_plural = "تقارير التشغيل الأسبوعية"
        ordering = ("-week_start", "screen__name")
        constraints = [
            models.UniqueConstraint(
                fields=("screen", "week_start"),
                name="uq_screen_uptime_week",
            )
        ]

    def __str__(self):
        return f"{self.screen} — {self.week_start} — {self.uptime_percent}%"


class SubscriptionPlan(models.Model):
    code = models.SlugField(
        "رمز الخطة",
        unique=True,
        max_length=50,
        help_text="يُستخدم داخليًا، مثل: basic, pro, enterprise",
    )
    name = models.CharField("اسم الخطة", max_length=150)
    price = models.DecimalField(
        "سعر الباقة (ر.س)",
        max_digits=8,
        decimal_places=2,
        default=0,
    )

    duration_days = models.PositiveIntegerField(
        "مدة الاشتراك (بالأيام)",
        null=True,
        blank=True,
        help_text="عند إنشاء اشتراك وترك تاريخ النهاية فارغًا: النهاية = تاريخ البداية + عدد الأيام.",
    )
    max_users = models.PositiveIntegerField(
        "الحد الأقصى للمستخدمين",
        null=True,
        blank=True,
        help_text="اتركه فارغًا لعدد غير محدود.",
    )
    max_screens = models.PositiveIntegerField(
        "الحد الأقصى لشاشات العرض",
        null=True,
        blank=True,
        help_text="اتركه فارغًا لعدد غير محدود.",
    )
    max_schools = models.PositiveIntegerField(
        "الحد الأقصى للمدارس",
        default=1,
        help_text="العدد المسموح به من المدارس في هذه الخطة.",
    )
    is_active = models.BooleanField(
        "متاحة للاشتراك",
        default=True,
    )
    sort_order = models.PositiveIntegerField(
        "ترتيب العرض",
        default=1,
    )

    class Meta:
        verbose_name = "خطة اشتراك"
        verbose_name_plural = "خطط الاشتراك"
        ordering = ("sort_order", "price")

    def __str__(self) -> str:
        return self.name


# ملاحظة: لا نضيف imports جديدة غير لازمة هنا
# لأن هذا الموديل أصبح Legacy ولا يجب أن يتداخل مع subscriptions
class SchoolSubscription(models.Model):
    """
    ⚠️ Legacy Model (للتوافق الخلفي فقط)

    هذا الموديل كان موجودًا سابقًا داخل core.
    الآن مصدر الحقيقة للاشتراك هو: subscriptions.SchoolSubscription

    ✅ مهم:
    - هذا الموديل لا يُحدّث School.is_active إطلاقًا (لتجنب التعارض).
    - لا تعتمد عليه في منطق صلاحيات/اشتراكات الداشبورد.
    """

    school = models.ForeignKey(
        "core.School",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="المدرسة",
    )
    plan = models.ForeignKey(
        "core.SubscriptionPlan",
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name="الخطة",
    )
    start_date = models.DateField("تاريخ البداية")
    end_date = models.DateField(
        "تاريخ الانتهاء",
        null=True,
        blank=True,
    )
    is_active = models.BooleanField(
        "نشط",
        default=True,
    )
    notes = models.CharField(
        "ملاحظات",
        max_length=250,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "اشتراك مدرسة (قديم)"
        verbose_name_plural = "اشتراكات المدارس (قديمة)"
        ordering = ("-start_date", "-created_at")

    def __str__(self) -> str:
        school_name = getattr(self.school, "name", str(self.school))
        plan_name = getattr(self.plan, "name", str(self.plan))
        return f"{school_name} — {plan_name}"

    @property
    def is_expired(self) -> bool:
        if self.end_date:
            return self.end_date < timezone.localdate()
        return False

    @property
    def status(self) -> str:
        if not self.is_active:
            return "موقوف"
        if self.is_expired:
            return "منتهي"
        return "نشط"

    def save(self, *args, **kwargs):
        """
        ✅ لا نقوم هنا بتحديث school.is_active
        لأن هذا كان سبب التعارض مع subscriptions.SchoolSubscription.
        """
        return super().save(*args, **kwargs)


class SupportTicket(models.Model):
    STATUS_CHOICES = [
        ("open", "مفتوحة"),
        ("in_progress", "قيد المعالجة"),
        ("closed", "مغلقة"),
    ]
    PRIORITY_CHOICES = [
        ("low", "منخفضة"),
        ("medium", "متوسطة"),
        ("high", "عالية"),
        ("urgent", "عاجلة"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="support_tickets",
        verbose_name="المستخدم",
    )
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        related_name="support_tickets",
        verbose_name="المدرسة",
        null=True,
        blank=True,
    )
    subject = models.CharField("الموضوع", max_length=200)
    message = models.TextField("الرسالة")
    status = models.CharField(
        "الحالة",
        max_length=20,
        choices=STATUS_CHOICES,
        default="open",
    )
    priority = models.CharField(
        "الأولوية",
        max_length=20,
        choices=PRIORITY_CHOICES,
        default="medium",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "تذكرة دعم"
        verbose_name_plural = "تذاكر الدعم"
        ordering = ["-created_at"]

    def __str__(self):
        return self.subject


class TicketComment(models.Model):
    ticket = models.ForeignKey(
        SupportTicket,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="التذكرة",
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        verbose_name="كاتب التعليق",
    )
    message = models.TextField("الرسالة")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "تعليق تذكرة"
        verbose_name_plural = "تعليقات التذاكر"
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment by {self.user} on {self.ticket}"

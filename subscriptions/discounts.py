"""التحقق من أكواد الخصم وتطبيقها وتسجيل استخدامها.

قواعد الاستخدام (قرارات المنتج):
- الكود واحد وله «عدد أكواد مصدرة» = إجمالي مرات الاستخدام؛ يتوقف عند النفاد.
- المدرسة الواحدة تستخدم الكود مرة واحدة فقط.
- الخصم يُطبق على الإجمالي كاملاً (الباقة + الشاشات الإضافية).

الاستخدام يُسجل عند اكتمال الدفع لا عند إنشاء الجلسة، حتى لا تستهلك الجلسات
المهجورة العدد المتاح. التحقق يعاد لحظة التسجيل بقفل صف الكود، فالسباق بين
جلستين متزامنتين لا يتجاوز الحد إلا لدفعة اكتملت فعلاً قبل النفاد.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from .models import DiscountCode, DiscountRedemption

logger = logging.getLogger(__name__)


class DiscountError(Exception):
    """كود غير قابل للاستخدام؛ الرسالة صالحة للعرض للمستخدم."""


@dataclass(frozen=True)
class DiscountQuote:
    code: DiscountCode
    original_total: Decimal
    discount_amount: Decimal
    final_total: Decimal


def _get_usable_code(raw_code: str, school, *, plan=None, for_update: bool = False) -> DiscountCode:
    normalized = DiscountCode.normalize(raw_code)
    if not normalized:
        raise DiscountError("أدخل كود الخصم.")

    qs = DiscountCode.objects.all()
    if for_update:
        qs = qs.select_for_update()
    try:
        code = qs.get(code=normalized)
    except DiscountCode.DoesNotExist:
        raise DiscountError("كود الخصم غير صحيح.")

    if not code.is_active:
        raise DiscountError("هذا الكود موقوف حالياً.")
    if not code.is_within_window():
        raise DiscountError("انتهت مدة السماح لهذا الكود.")
    if code.remaining_uses <= 0:
        raise DiscountError("انتهى عدد استخدامات هذا الكود.")
    if plan is not None and not code.allows_plan(plan):
        raise DiscountError("هذا الكود غير متاح للباقة المختارة.")
    if school is not None and DiscountRedemption.objects.filter(
        discount_code=code, school=school
    ).exists():
        raise DiscountError("سبق لمدرستك استخدام هذا الكود.")
    return code


def quote_discount(raw_code: str, school, total: Decimal, *, plan=None) -> DiscountQuote:
    """تحقق من الكود واحسب الخصم على إجمالي معيّن دون تسجيل استخدام."""
    code = _get_usable_code(raw_code, school, plan=plan)
    original = Decimal(total or 0).quantize(Decimal("0.01"))
    cut = code.discount_for(original)
    if cut <= 0:
        raise DiscountError("لا ينطبق هذا الكود على قيمة الطلب الحالية.")
    return DiscountQuote(
        code=code,
        original_total=original,
        discount_amount=cut,
        final_total=(original - cut).quantize(Decimal("0.01")),
    )


def record_redemption(checkout) -> DiscountRedemption | None:
    """سجل استخدام كود جلسة دفع مكتملة (idempotent).

    يُستدعى بعد نجاح الدفع. الدفعة اكتملت بالمبلغ المخصوم فعلاً، لذا يُسجل
    الاستخدام حتى لو نفد العدد بين إنشاء الجلسة وإتمام الدفع — مع تحذير في
    السجل ليظهر التجاوز في المراجعة بدل أن يضيع صامتاً.
    """
    code_id = getattr(checkout, "discount_code_id", None)
    if not code_id:
        return None

    existing = DiscountRedemption.objects.filter(checkout=checkout).first()
    if existing is not None:
        return existing

    with transaction.atomic():
        code = DiscountCode.objects.select_for_update().get(pk=code_id)
        if code.remaining_uses <= 0:
            logger.warning(
                "discount_redemption_over_limit code=%s checkout=%s",
                code.code,
                checkout.merchant_reference,
            )
        redemption, _created = DiscountRedemption.objects.get_or_create(
            discount_code=code,
            school=checkout.school,
            defaults={
                "checkout": checkout,
                "amount_discounted": checkout.discount_amount or Decimal("0.00"),
            },
        )
    return redemption

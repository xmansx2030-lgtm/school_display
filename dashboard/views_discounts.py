"""عروض مدير المنصة لأكواد الخصم: إنشاء الأكواد وتحديد مدتها وعددها وإيقافها."""

from __future__ import annotations

from .view_helpers import *  # noqa: F401,F403  (shared view layer)

# =========================
# أكواد الخصم (مدير المنصة)
# =========================

@system_permission_required("discounts.view")
def system_discounts_list(request):
    from subscriptions.models import DiscountCode

    q = (request.GET.get("q") or "").strip()
    state = (request.GET.get("state") or "").strip()
    now = timezone.now()

    codes = DiscountCode.objects.prefetch_related("plans").annotate(
        redemptions_count=Count("redemptions", distinct=True),
    )
    if q:
        codes = codes.filter(Q(code__icontains=q) | Q(notes__icontains=q))
    if state == "active":
        codes = codes.filter(is_active=True)
    elif state == "inactive":
        codes = codes.filter(is_active=False)

    codes = list(codes.order_by("-created_at"))
    for code in codes:
        code.remaining = max(0, int(code.max_uses) - int(code.redemptions_count or 0))
        code.usable_now = bool(
            code.is_active and code.is_within_window(now) and code.remaining > 0
        )

    all_codes = DiscountCode.objects.all()
    context = {
        "codes": codes,
        "q": q,
        "state": state,
        "codes_count": all_codes.count(),
        "active_codes_count": all_codes.filter(is_active=True).count(),
        "total_redemptions": sum(int(c.redemptions_count or 0) for c in codes),
    }
    return render(request, "admin/discounts_list.html", context)


@system_permission_required("discounts.manage")
def system_discount_create(request):
    from .forms import DiscountCodeForm

    if request.method == "POST":
        form = DiscountCodeForm(request.POST)
        if form.is_valid():
            discount = form.save(commit=False)
            discount.created_by = request.user
            discount.save()
            form.save_m2m()
            messages.success(request, f"تمت إضافة كود الخصم «{discount.code}» بنجاح.")
            return redirect("dashboard:system_discounts_list")
    else:
        form = DiscountCodeForm()
    return render(
        request,
        "admin/discount_form.html",
        {"form": form, "title": "إضافة كود خصم", "submit_label": "إضافة الكود"},
    )


@system_permission_required("discounts.manage")
def system_discount_edit(request, pk):
    from subscriptions.models import DiscountCode

    from .forms import DiscountCodeForm

    discount = get_object_or_404(DiscountCode, pk=pk)
    if request.method == "POST":
        form = DiscountCodeForm(request.POST, instance=discount)
        if form.is_valid():
            form.save()
            messages.success(request, f"تم تحديث كود الخصم «{discount.code}» بنجاح.")
            return redirect("dashboard:system_discounts_list")
    else:
        form = DiscountCodeForm(instance=discount)
    return render(
        request,
        "admin/discount_form.html",
        {
            "form": form,
            "title": f"تعديل كود «{discount.code}»",
            "discount": discount,
            "submit_label": "حفظ التعديلات",
        },
    )


@login_required
@system_permission_required("discounts.manage")
@require_POST
def system_discount_toggle(request, pk):
    from subscriptions.models import DiscountCode

    discount = get_object_or_404(DiscountCode, pk=pk)
    discount.is_active = not discount.is_active
    discount.save(update_fields=["is_active", "updated_at"])
    if discount.is_active:
        messages.success(request, f"تم تفعيل كود «{discount.code}».")
    else:
        messages.warning(request, f"تم إيقاف كود «{discount.code}» ولن يقبل استخدامات جديدة.")
    return redirect("dashboard:system_discounts_list")


@system_permission_required("discounts.manage")
def system_discount_delete(request, pk):
    from subscriptions.models import DiscountCode

    discount = get_object_or_404(DiscountCode, pk=pk)
    linked = discount.redemptions.count() + discount.checkouts.count()

    if request.method == "POST":
        if linked:
            if discount.is_active:
                discount.is_active = False
                discount.save(update_fields=["is_active", "updated_at"])
            messages.warning(
                request,
                f"تعذر حذف كود «{discount.code}» لارتباطه بعمليات دفع؛ تم إيقافه بدلاً من حذفه.",
            )
            return redirect("dashboard:system_discounts_list")
        try:
            code_text = discount.code
            discount.delete()
        except ProtectedError:
            discount.is_active = False
            discount.save(update_fields=["is_active", "updated_at"])
            messages.warning(
                request,
                f"تعذر حذف كود «{discount.code}» لوجود سجلات مرتبطة؛ تم إيقافه بدلاً من ذلك.",
            )
            return redirect("dashboard:system_discounts_list")
        messages.success(request, f"تم حذف كود «{code_text}» نهائياً.")
        return redirect("dashboard:system_discounts_list")

    return render(
        request,
        "admin/discount_confirm_delete.html",
        {"discount": discount, "linked_count": linked},
    )

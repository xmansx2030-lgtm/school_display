"""Platform payment ledger and read-only payment details."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.utils.dateparse import parse_date

from subscriptions.models import SubscriptionPaymentOperation, SubscriptionRefund

from .decorators import system_permission_required


@system_permission_required("subscriptions.view")
def system_payments_list(request):
    """Platform ledger for completed/recorded subscription payments."""

    q = (request.GET.get("q") or "").strip()
    method = (request.GET.get("method") or "").strip()
    source = (request.GET.get("source") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    qs = (
        SubscriptionPaymentOperation.objects.select_related(
            "school",
            "plan",
            "subscription",
            "created_by",
            "invoice",
            "moyasar_checkout",
            "tamara_checkout",
        )
        .prefetch_related("refunds")
        .order_by("-created_at", "-id")
    )

    if q:
        search_filter = (
            Q(school__name__icontains=q)
            | Q(plan__name__icontains=q)
            | Q(note__icontains=q)
            | Q(invoice__invoice_number__icontains=q)
            | Q(moyasar_checkout__merchant_reference__icontains=q)
            | Q(moyasar_checkout__payment_id__icontains=q)
            | Q(tamara_checkout__merchant_reference__icontains=q)
            | Q(tamara_checkout__tamara_order_id__icontains=q)
        )
        if q.isdigit():
            search_filter |= Q(pk=int(q))
        qs = qs.filter(search_filter).distinct()
    valid_methods = {value for value, _label in SubscriptionPaymentOperation.METHOD_CHOICES}
    valid_sources = {value for value, _label in SubscriptionPaymentOperation.SOURCE_CHOICES}
    if method in valid_methods:
        qs = qs.filter(method=method)
    if source in valid_sources:
        qs = qs.filter(source=source)
    parsed_from = parse_date(date_from) if date_from else None
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_from:
        qs = qs.filter(created_at__date__gte=parsed_from)
    if parsed_to:
        qs = qs.filter(created_at__date__lte=parsed_to)

    totals = qs.aggregate(total_amount=Sum("amount"), payment_count=Count("id"))
    total_amount = totals["total_amount"] or Decimal("0.00")
    refund_total = (
        SubscriptionRefund.objects.filter(operation__in=qs, status="completed")
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)
    rows = []
    for operation in page_obj.object_list:
        completed_refunds = sum(
            (refund.amount for refund in operation.refunds.all() if refund.status == "completed"),
            Decimal("0.00"),
        )
        rows.append(
            {
                "operation": operation,
                "refunded_amount": completed_refunds,
                "net_amount": operation.amount - completed_refunds,
            }
        )

    return render(
        request,
        "admin/payments_list.html",
        {
            "rows": rows,
            "page_obj": page_obj,
            "q": q,
            "method": method,
            "source": source,
            "date_from": date_from,
            "date_to": date_to,
            "method_choices": SubscriptionPaymentOperation.METHOD_CHOICES,
            "source_choices": SubscriptionPaymentOperation.SOURCE_CHOICES,
            "payment_count": totals["payment_count"] or 0,
            "total_amount": total_amount,
            "refund_total": refund_total,
            "net_amount": total_amount - refund_total,
        },
    )


@system_permission_required("subscriptions.view")
def system_payment_detail(request, pk: int):
    """Read-only accounting and gateway details for one recorded payment."""

    operation = get_object_or_404(
        SubscriptionPaymentOperation.objects.select_related(
            "school", "plan", "subscription", "created_by", "invoice"
        ).prefetch_related("refunds"),
        pk=pk,
    )

    gateway = None
    try:
        checkout = operation.moyasar_checkout
        gateway = {
            "provider": "ميسر",
            "reference": checkout.merchant_reference,
            "payment_id": checkout.payment_id,
            "status": checkout.status,
            "status_label": checkout.get_status_display(),
            "request_type": checkout.get_request_type_display(),
            "last_event": checkout.last_event,
            "error_message": checkout.error_message,
            "live_mode": checkout.live_mode,
            "processed_at": checkout.processed_at,
            "created_at": checkout.created_at,
            "updated_at": checkout.updated_at,
            "discount_code": checkout.discount_code,
            "discount_amount": checkout.discount_amount,
        }
    except ObjectDoesNotExist:
        try:
            checkout = operation.tamara_checkout
            gateway = {
                "provider": "تمارا",
                "reference": checkout.merchant_reference,
                "payment_id": checkout.tamara_order_id or checkout.checkout_id,
                "status": checkout.status,
                "status_label": checkout.get_status_display(),
                "request_type": checkout.get_request_type_display(),
                "last_event": checkout.last_event,
                "error_message": checkout.error_message,
                "live_mode": True,
                "processed_at": checkout.processed_at,
                "created_at": checkout.created_at,
                "updated_at": checkout.updated_at,
                "discount_code": None,
                "discount_amount": Decimal("0.00"),
            }
        except ObjectDoesNotExist:
            pass

    refunds = list(operation.refunds.all())
    completed_refund_total = sum(
        (refund.amount for refund in refunds if refund.status == "completed"),
        Decimal("0.00"),
    )
    return render(
        request,
        "admin/payment_detail.html",
        {
            "operation": operation,
            "gateway": gateway,
            "refunds": refunds,
            "completed_refund_total": completed_refund_total,
            "net_amount": operation.amount - completed_refund_total,
        },
    )

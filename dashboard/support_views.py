from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from core.models import SupportTicket

from .access import active_school_for_user, has_system_permission
from .decorators import system_permission_required
from .forms import CustomerSupportTicketForm, SupportTicketForm, TicketCommentForm


@system_permission_required("support.view")
def system_support_tickets(request):
    tickets = SupportTicket.objects.all().order_by("-created_at")
    return render(request, "admin/support_tickets.html", {"tickets": tickets})


@system_permission_required("support.view")
def system_support_ticket_detail(request, pk: int):
    ticket = get_object_or_404(SupportTicket, pk=pk)
    if request.method == "POST":
        if not has_system_permission(request.user, "support.manage"):
            raise PermissionDenied("لا تملك صلاحية معالجة تذاكر الدعم.")
        if "status" in request.POST:
            valid_statuses = {status for status, _label in SupportTicket.STATUS_CHOICES}
            new_status = request.POST.get("status") or ""
            if new_status not in valid_statuses:
                messages.error(request, "حالة غير صالحة.")
                return redirect("dashboard:system_support_ticket_detail", pk=pk)
            ticket.status = new_status
            ticket.save(update_fields=["status", "updated_at"])
            messages.success(request, "تم تحديث حالة التذكرة.")
            return redirect("dashboard:system_support_ticket_detail", pk=pk)

        comment_form = TicketCommentForm(request.POST)
        if comment_form.is_valid():
            comment = comment_form.save(commit=False)
            comment.ticket = ticket
            comment.user = request.user
            comment.save()
            messages.success(request, "تم إضافة الرد بنجاح.")
            return redirect("dashboard:system_support_ticket_detail", pk=pk)
    else:
        comment_form = TicketCommentForm()

    return render(
        request,
        "admin/support_ticket_detail.html",
        {
            "ticket": ticket,
            "comment_form": comment_form,
            "can_manage_support": has_system_permission(request.user, "support.manage"),
        },
    )


@system_permission_required("support.manage")
def system_support_ticket_create(request):
    if request.method == "POST":
        form = SupportTicketForm(request.POST)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.user = request.user
            ticket.save()
            messages.success(request, "تم فتح التذكرة بنجاح")
            return redirect("dashboard:system_support_tickets")
    else:
        form = SupportTicketForm()
    return render(request, "admin/support_ticket_form.html", {"form": form})


@login_required(login_url="dashboard:login")
def customer_support_tickets(request):
    tickets = SupportTicket.objects.filter(user=request.user).order_by("-created_at")
    return render(request, "dashboard/support_tickets.html", {"tickets": tickets})


@login_required(login_url="dashboard:login")
def customer_support_ticket_detail(request, pk: int):
    ticket = get_object_or_404(SupportTicket, pk=pk, user=request.user)
    if request.method == "POST":
        comment_form = TicketCommentForm(request.POST)
        if comment_form.is_valid():
            comment = comment_form.save(commit=False)
            comment.ticket = ticket
            comment.user = request.user
            comment.save()
            messages.success(request, "تم إضافة الرد بنجاح.")
            return redirect("dashboard:customer_support_ticket_detail", pk=pk)
    else:
        comment_form = TicketCommentForm()

    return render(
        request,
        "dashboard/support_ticket_detail.html",
        {"ticket": ticket, "comment_form": comment_form},
    )


@login_required(login_url="dashboard:login")
def customer_support_ticket_create(request):
    if request.method == "POST":
        form = CustomerSupportTicketForm(request.POST, user=request.user)
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.user = request.user
            ticket.school = active_school_for_user(request.user)
            ticket.save()
            messages.success(request, "تم فتح التذكرة بنجاح")
            return redirect("dashboard:customer_support_tickets")
    else:
        initial = {}
        subject = (request.GET.get("subject") or "").strip()
        message = (request.GET.get("message") or "").strip()
        if subject:
            initial["subject"] = subject
        if message:
            initial["message"] = message
        form = CustomerSupportTicketForm(user=request.user, initial=initial)
    return render(request, "dashboard/support_ticket_form.html", {"form": form})

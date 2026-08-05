from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from core.models import SupportTicket

from .access import active_school_for_user, has_system_permission, is_system_staff_user
from .decorators import system_permission_required
from .forms import CustomerSupportTicketForm, SupportTicketForm, TicketCommentForm


@system_permission_required("support.view")
def system_support_tickets(request):
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()
    priority = (request.GET.get("priority") or "").strip()

    valid_statuses = {code for code, _label in SupportTicket.STATUS_CHOICES}
    valid_priorities = {code for code, _label in SupportTicket.PRIORITY_CHOICES}

    tickets = SupportTicket.objects.select_related("user", "school").order_by("-created_at")

    if q:
        tickets = tickets.filter(
            Q(subject__icontains=q)
            | Q(message__icontains=q)
            | Q(user__username__icontains=q)
            | Q(user__email__icontains=q)
            | Q(school__name__icontains=q)
        )
    if status in valid_statuses:
        tickets = tickets.filter(status=status)
    else:
        status = ""
    if priority in valid_priorities:
        tickets = tickets.filter(priority=priority)
    else:
        priority = ""

    counters = SupportTicket.objects.all()
    paginator = Paginator(tickets, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(
        request,
        "admin/support_tickets.html",
        {
            "tickets": page_obj.object_list,
            "page_obj": page_obj,
            "q": q,
            "status": status,
            "priority": priority,
            "status_choices": SupportTicket.STATUS_CHOICES,
            "priority_choices": SupportTicket.PRIORITY_CHOICES,
            "open_count": counters.filter(status="open").count(),
            "in_progress_count": counters.filter(status="in_progress").count(),
            "closed_count": counters.filter(status="closed").count(),
            "urgent_count": counters.exclude(status="closed")
            .filter(priority__in=("high", "urgent"))
            .count(),
            "filtered_count": paginator.count,
        },
    )


@system_permission_required("support.view")
def system_support_ticket_detail(request, pk: int):
    ticket = get_object_or_404(
        SupportTicket.objects.select_related("user", "user__profile", "school"), pk=pk
    )
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

    # The platform-reply badge inspects the author's employee profile and
    # groups; fetch both up front so a long thread stays at a fixed query cost.
    comments = list(
        ticket.comments.select_related("user", "user__system_employee_profile")
        .prefetch_related("user__groups")
        .order_by("created_at")
    )
    for comment in comments:
        comment.is_platform_reply = is_system_staff_user(comment.user)

    return render(
        request,
        "admin/support_ticket_detail.html",
        {
            "ticket": ticket,
            "comments": comments,
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

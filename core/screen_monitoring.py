from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import DisplayScreen, ScreenOutage, ScreenWeeklyUptimeReport
from schedule.models import SchoolSettings
from telegram_alerts.services import queue_alert


User = get_user_model()


def _manager_emails(school) -> list[str]:
    return list(
        User.objects.filter(profile__schools=school)
        .exclude(email="")
        .values_list("email", flat=True)
        .distinct()
    )


def _send_outage_alert(screen, outage, *, now):
    school = screen.school
    last_seen = screen.last_seen
    last_seen_label = timezone.localtime(last_seen).strftime("%Y-%m-%d %H:%M") if last_seen else "لم تتصل بعد"
    subject = f"تعطل شاشة: {screen.name} — {school.name}"
    body = (
        f"لم تتصل الشاشة «{screen.name}» منذ المدة المحددة.\n"
        f"المدرسة: {school.name}\n"
        f"آخر ظهور: {last_seen_label}\n"
        "يرجى التحقق من الكهرباء والإنترنت وجهاز العرض."
    )
    try:
        school_settings = school.schedule_settings
    except SchoolSettings.DoesNotExist:
        school_settings = None
    if school_settings is None or school_settings.screen_offline_email_enabled:
        recipients = _manager_emails(school)
        if recipients:
            send_mail(
                subject,
                body,
                getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipients,
                fail_silently=True,
            )
    queue_alert(
        event_type="screen_offline",
        dedupe_key=f"screen-offline:{outage.pk}",
        message=(
            f"<b>🔴 تعطل شاشة عرض</b>\n\n"
            f"🏫 المدرسة: <b>{school.name}</b>\n"
            f"🖥 الشاشة: <b>{screen.name}</b>\n"
            f"🕒 آخر ظهور: <code>{last_seen_label}</code>"
        ),
        action_url=f"/dashboard/screens/",
        action_label="فتح الشاشات",
    )
    outage.alert_sent_at = now
    outage.save(update_fields=("alert_sent_at",))


def scan_screens(*, now=None) -> dict:
    now = now or timezone.now()
    opened = resolved = alerted = 0
    screens = (
        DisplayScreen.objects.filter(is_active=True, school__isnull=False)
        .exclude(bound_device_id__isnull=True)
        .exclude(bound_device_id="")
        .select_related("school", "school__schedule_settings")
    )
    for screen in screens.iterator():
        try:
            school_settings = screen.school.schedule_settings
        except SchoolSettings.DoesNotExist:
            continue
        if not school_settings.screen_offline_alerts_enabled:
            continue
        threshold = max(5, int(school_settings.screen_offline_threshold_minutes or 10))
        cutoff = now - timedelta(minutes=threshold)
        # A newly-bound screen gets the full grace period before the first alert.
        # For screens that have connected before, last_seen is the outage start.
        offline_since = screen.last_seen or screen.bound_at or screen.created_at
        is_offline = offline_since < cutoff
        with transaction.atomic():
            outage = (
                ScreenOutage.objects.select_for_update()
                .filter(screen=screen, resolved_at__isnull=True)
                .order_by("-detected_at")
                .first()
            )
            if is_offline:
                if outage is None:
                    outage = ScreenOutage.objects.create(
                        screen=screen,
                        detected_at=now,
                        last_seen_at=offline_since,
                    )
                    opened += 1
                if outage.alert_sent_at is None:
                    _send_outage_alert(screen, outage, now=now)
                    alerted += 1
            elif outage is not None:
                outage.resolved_at = now
                outage.save(update_fields=("resolved_at",))
                resolved += 1
    return {"opened": opened, "resolved": resolved, "alerted": alerted}


def _period_datetimes(week_start):
    current_tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(week_start, time.min), current_tz)
    end = start + timedelta(days=7)
    return start, end


def _offline_seconds(screen, start, end):
    total = 0
    outages = ScreenOutage.objects.filter(
        screen=screen,
        detected_at__lt=end,
    ).filter(Q(resolved_at__isnull=True) | Q(resolved_at__gt=start))
    for outage in outages:
        overlap_start = max(outage.last_seen_at or outage.detected_at, start)
        overlap_end = min(outage.resolved_at or end, end)
        if overlap_end > overlap_start:
            total += int((overlap_end - overlap_start).total_seconds())
    return min(total, int((end - start).total_seconds()))


def send_weekly_uptime_reports(*, week_start=None, now=None) -> dict:
    now = now or timezone.now()
    local_today = timezone.localdate(now)
    if week_start is None:
        current_week_start = local_today - timedelta(days=local_today.weekday())
        week_start = current_week_start - timedelta(days=7)
    week_end = week_start + timedelta(days=6)
    start, end = _period_datetimes(week_start)
    period_seconds = int((end - start).total_seconds())
    schools_sent = reports_created = 0

    for school_settings in SchoolSettings.objects.filter(
        weekly_uptime_report_enabled=True,
        school__isnull=False,
    ).select_related("school"):
        school = school_settings.school
        reports = []
        for screen in DisplayScreen.objects.filter(school=school, is_active=True).order_by("name"):
            offline = _offline_seconds(screen, start, end)
            percent = Decimal(period_seconds - offline) * Decimal(100) / Decimal(period_seconds)
            report, created = ScreenWeeklyUptimeReport.objects.update_or_create(
                screen=screen,
                week_start=week_start,
                defaults={
                    "week_end": week_end,
                    "offline_seconds": offline,
                    "uptime_percent": percent.quantize(Decimal("0.01")),
                },
            )
            reports_created += int(created)
            reports.append(report)
        if not reports or all(report.sent_at for report in reports):
            continue
        lines = [
            f"تقرير تشغيل شاشات {school.name}",
            f"الفترة: {week_start} إلى {week_end}",
            "",
        ]
        lines.extend(
            f"- {report.screen.name}: {report.uptime_percent}%"
            for report in reports
        )
        recipients = _manager_emails(school)
        if recipients:
            send_mail(
                f"تقرير تشغيل الشاشات الأسبوعي — {school.name}",
                "\n".join(lines),
                getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipients,
                fail_silently=True,
            )
        queue_alert(
            event_type="screen_uptime_weekly",
            dedupe_key=f"screen-uptime-weekly:{school.pk}:{week_start.isoformat()}",
            message="<b>📊 تقرير تشغيل الشاشات الأسبوعي</b>\n\n" + "\n".join(lines[1:]),
            action_url="/dashboard/screens/",
            action_label="فتح الشاشات",
        )
        ScreenWeeklyUptimeReport.objects.filter(pk__in=[item.pk for item in reports]).update(sent_at=now)
        schools_sent += 1
    return {"schools_sent": schools_sent, "reports_created": reports_created}

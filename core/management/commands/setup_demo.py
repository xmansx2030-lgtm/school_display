from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import DisplayScreen, School, SubscriptionPlan, UserProfile
from notices.models import Announcement, Excellence
from schedule.models import (
    Break,
    ClassLesson,
    DaySchedule,
    DutyAssignment,
    Period,
    SchoolClass,
    SchoolSettings,
    Subject,
    Teacher,
)
from standby.models import StandbyAssignment
from subscriptions.models import SchoolSubscription


class Command(BaseCommand):
    help = "Sets up or resets a polished demo account with school display data."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="demo_user")
        parser.add_argument("--password", default="demo_password_123")
        parser.add_argument("--email", default="demo@school-display.local")

    @transaction.atomic
    def handle(self, *args, **options):
        username = options["username"]
        password = options["password"]
        email = options["email"]
        school_name = "مدارس البيان الأهلية"
        school_slug = "demo-school"

        self.stdout.write("Resetting demo data...")
        User.objects.filter(username=username).delete()
        School.objects.filter(slug=school_slug).delete()

        plan, _ = SubscriptionPlan.objects.update_or_create(
            code="demo-school-display",
            defaults={
                "name": "باقة العرض التجريبية",
                "description": "باقة داخلية لالتقاط لقطات المنتج وتجربة النظام.",
                "price": Decimal("0.00"),
                "duration_days": 365,
                "max_users": 5,
                "max_screens": 5,
                "is_active": True,
                "sort_order": 1,
                "card_features": "جميع مزايا النظام كاملة\nحتى 5 شاشات عرض\nدعم بيانات تجريبية",
                "card_cta_text": "ابدأ التجربة",
            },
        )

        school = School.objects.create(
            name=school_name,
            slug=school_slug,
            school_type="boys",
            is_active=True,
        )
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name="مدير",
            last_name="المدرسة",
            is_active=True,
        )
        profile = UserProfile.objects.create(
            user=user,
            active_school=school,
            mobile="0500000000",
            needs_onboarding=False,
            email_verified_at=timezone.now(),
        )
        profile.schools.add(school)

        today = timezone.localdate()
        SchoolSubscription.objects.create(
            school=school,
            plan=plan,
            starts_at=today,
            ends_at=today + timedelta(days=365),
            status="active",
            notes="اشتراك تجريبي لتجهيز لقطات صفحة الهبوط.",
        )

        settings = SchoolSettings.objects.create(
            school=school,
            name=school_name,
            theme=SchoolSettings.THEME_CYAN,
            display_accent_color="#0F766E",
            featured_panel=SchoolSettings.FEATURE_PANEL_EXCELLENCE,
            refresh_interval_sec=30,
            timezone_name="Asia/Riyadh",
            standby_scroll_speed=0.65,
            periods_scroll_speed=0.45,
            display_before_title="أهلا بكم في يوم دراسي منظم",
            display_after_title="أحسنتم اليوم، ونلقاكم غدا بإذن الله",
        )

        screens = [
            DisplayScreen.objects.create(
                school=school,
                name="شاشة المدخل الرئيسي",
                is_active=True,
                theme_override="cyan",
                featured_panel_override="excellence",
                last_seen=timezone.now(),
            ),
            DisplayScreen.objects.create(
                school=school,
                name="شاشة غرفة المعلمين",
                is_active=True,
                theme_override="emerald",
                featured_panel_override="duty",
                last_seen=timezone.now() - timedelta(minutes=1),
            ),
            DisplayScreen.objects.create(
                school=school,
                name="شاشة الأنشطة",
                is_active=True,
                occasion_theme="auto",
            ),
        ]

        class_names = ["الأول متوسط أ", "الثاني متوسط ب", "الثالث متوسط أ", "الأول ثانوي ب"]
        subject_names = ["الرياضيات", "اللغة العربية", "العلوم", "الإنجليزي", "الحاسب", "الدراسات الإسلامية"]
        teacher_names = ["أ. خالد العتيبي", "أ. أحمد الغامدي", "أ. ماجد الشهري", "أ. يوسف الحربي", "أ. فهد الدوسري", "أ. عبدالله المالكي"]

        classes = [SchoolClass.objects.create(settings=settings, name=name) for name in class_names]
        subjects = [Subject.objects.create(school=school, name=name) for name in subject_names]
        teachers = [Teacher.objects.create(school=school, name=name) for name in teacher_names]

        weekdays = [7, 1, 2, 3, 4]
        for weekday in weekdays:
            day = DaySchedule.objects.create(
                settings=settings,
                weekday=weekday,
                is_active=True,
                periods_count=6,
            )
            current_dt = datetime.combine(today, time(7, 0))
            for index in range(1, 7):
                end_dt = current_dt + timedelta(minutes=45)
                Period.objects.create(
                    day=day,
                    index=index,
                    starts_at=current_dt.time(),
                    ends_at=end_dt.time(),
                )
                if index == 3:
                    Break.objects.create(day=day, label="الفسحة", starts_at=end_dt.time(), duration_min=20)
                    current_dt = end_dt + timedelta(minutes=25)
                else:
                    current_dt = end_dt + timedelta(minutes=5)

            for period_index in range(1, 7):
                for class_offset, school_class in enumerate(classes):
                    subject = subjects[(period_index + class_offset + weekday) % len(subjects)]
                    teacher = teachers[(period_index + (class_offset * 2) + weekday) % len(teachers)]
                    ClassLesson.objects.create(
                        settings=settings,
                        school_class=school_class,
                        weekday=weekday,
                        period_index=period_index,
                        subject=subject,
                        teacher=teacher,
                        is_active=True,
                    )

        Announcement.objects.create(
            school=school,
            title="تكريم الطلاب المتفوقين",
            body="سيقام التكريم بعد الطابور الصباحي في بهو المدرسة.",
            level="success",
            starts_at=timezone.now() - timedelta(hours=1),
            expires_at=timezone.now() + timedelta(days=5),
            is_active=True,
        )
        Announcement.objects.create(
            school=school,
            title="تذكير لاختبار القدرات",
            body="تبدأ ورشة الاستعداد للاختبار في مركز مصادر التعلم بعد الحصة الخامسة.",
            level="warning",
            starts_at=timezone.now() - timedelta(hours=2),
            expires_at=timezone.now() + timedelta(days=3),
            is_active=True,
        ).screens.add(screens[1])
        Announcement.objects.create(
            school=school,
            title="برنامج اليوم الوطني",
            body="فعالية وطنية على شاشة الأنشطة طوال هذا الأسبوع.",
            level="info",
            occasion_theme="saudi_national_day",
            starts_at=timezone.now() - timedelta(days=1),
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        ).screens.add(screens[2])

        Excellence.objects.create(
            school=school,
            teacher_name="أ. أحمد الغامدي",
            reason="مبادرة إثرائية في مادة العلوم",
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=10),
            priority=1,
        )
        Excellence.objects.create(
            school=school,
            teacher_name="الطالب يزن القحطاني",
            reason="المركز الأول في مسابقة الرياضيات",
            start_at=timezone.now() - timedelta(days=1),
            end_at=timezone.now() + timedelta(days=10),
            priority=2,
        )

        StandbyAssignment.objects.bulk_create(
            [
                StandbyAssignment(school=school, date=today, period_index=2, class_name="الثاني متوسط ب", teacher_name="أ. فهد الدوسري", notes="قاعة 12"),
                StandbyAssignment(school=school, date=today, period_index=4, class_name="الأول ثانوي ب", teacher_name="أ. يوسف الحربي", notes="معمل الحاسب"),
                StandbyAssignment(school=school, date=today, period_index=5, class_name="الثالث متوسط أ", teacher_name="أ. عبدالله المالكي", notes="بدل غياب"),
            ]
        )
        DutyAssignment.objects.bulk_create(
            [
                DutyAssignment(school=school, date=today, teacher_name="أ. خالد العتيبي", duty_type=DutyAssignment.DUTY_SUPERVISION, location="البوابة الرئيسية", priority=1),
                DutyAssignment(school=school, date=today, teacher_name="أ. ماجد الشهري", duty_type=DutyAssignment.DUTY_DUTY, location="الساحة الداخلية", priority=2),
            ]
        )

        self.stdout.write(self.style.SUCCESS("Demo account is ready."))
        self.stdout.write(f"Username: {username}")
        self.stdout.write(f"Password: {password}")
        self.stdout.write(f"School: {school.name}")
        self.stdout.write("Dashboard: /dashboard/login/")
        self.stdout.write(f"Display preview: /s/{screens[0].short_code}/?preview=1")

# schedule/admin.py
from __future__ import annotations

from django.contrib import admin
from django import forms
from django.utils.html import format_html

from .models import (
    SchoolSettings,
    DutyAssignment,
    SchoolClass,
    Subject,
    Teacher,
    DaySchedule,
    SchoolClosure,
    Period,
    Break,
    ClassLesson,
)


class SchoolSettingsAdminForm(forms.ModelForm):
    class Meta:
        model = SchoolSettings
        fields = "__all__"
        widgets = {
            "display_before_title": forms.TextInput(attrs={"style": "width: 40em;", "dir": "rtl"}),
            "display_before_badge": forms.TextInput(attrs={"style": "width: 20em;", "dir": "rtl"}),
            "display_after_title": forms.TextInput(attrs={"style": "width: 40em;", "dir": "rtl"}),
            "display_after_badge": forms.TextInput(attrs={"style": "width: 20em;", "dir": "rtl"}),
            "display_after_holiday_title": forms.TextInput(attrs={"style": "width: 40em;", "dir": "rtl"}),
            "display_after_holiday_badge": forms.TextInput(attrs={"style": "width: 20em;", "dir": "rtl"}),
            "display_holiday_title": forms.TextInput(attrs={"style": "width: 40em;", "dir": "rtl"}),
            "display_holiday_badge": forms.TextInput(attrs={"style": "width: 20em;", "dir": "rtl"}),
        }


# ============================================================
# School Settings
# ============================================================

@admin.register(SchoolSettings)
class SchoolSettingsAdmin(admin.ModelAdmin):
    form = SchoolSettingsAdminForm
    list_display = (
        "name",
        "school",
        "theme",
        "featured_panel",
        "timezone_name",
        "refresh_interval_sec",
        "display_messages_summary",
        "test_mode_weekday_override",
    )
    list_filter = ("theme", "featured_panel", "timezone_name", "test_mode_weekday_override")
    search_fields = ("name", "school__name")
    autocomplete_fields = ("school",)
    ordering = ("name",)
    
    fieldsets = (
        ("معلومات أساسية", {
            "fields": ("school", "name", "logo_url")
        }),
        ("المظهر", {
            "fields": ("theme", "display_accent_color", "featured_panel")
        }),
        ("إعدادات العرض", {
            "fields": ("timezone_name", "refresh_interval_sec", "standby_scroll_speed", "periods_scroll_speed")
        }),
        ("نصوص شاشة العرض قبل وبعد الدوام", {
            "fields": (
                "display_messages_preview",
                "display_before_title",
                "display_before_badge",
                "display_after_title",
                "display_after_badge",
                "display_after_holiday_title",
                "display_after_holiday_badge",
                "display_holiday_title",
                "display_holiday_badge",
            ),
            "description": (
                "عدّل النصوص التي تظهر على شاشة العرض في حالتي ما قبل بداية اليوم الدراسي "
                "وبعد انتهائه، وكذلك في حالتي ما بعد الدوام قبل الإجازة ويوم الإجازة نفسه. "
                "هذه الحقول هي المصدر الصريح للنص الظاهر على الشاشة."
            ),
        }),
        ("رؤية الأقسام", {
            "fields": (
                "show_home", "show_settings", "show_change_password", "show_screens",
                "show_days", "show_announcements", "show_excellence", "show_standby",
                "show_lessons", "show_school_data"
            ),
            "classes": ("collapse",)
        }),
        ("الإصدارات والتحديثات", {
            "fields": ("schedule_revision",)
        }),
        ("⚠️ وضع الاختبار (للسوبر أدمن فقط)", {
            "fields": ("test_mode_weekday_override",),
            "classes": ("collapse",),
            "description": (
                "<strong style='color: #d97706;'>تحذير:</strong> "
                "هذا الحقل للاختبار فقط. لتشغيل الشاشة في يوم إجازة، حدد اليوم المراد محاكاته. "
                "مثال: لو اليوم خميس (إجازة) وتريد اختبار جدول الأحد، اختر 'الأحد' من القائمة. "
                "<br><strong>لا تنسَ إلغاء التفعيل بعد الاختبار!</strong>"
            )
        }),
    )
    
    def get_readonly_fields(self, request, obj=None):
        # schedule_revision للقراءة فقط (يتحدث تلقائياً)
        readonly = ["schedule_revision", "display_messages_preview"]
        
        # test_mode_weekday_override للسوبر أدمن فقط
        if not request.user.is_superuser:
            readonly.append("test_mode_weekday_override")
        
        return readonly

    @admin.display(description="ملخص النصوص")
    def display_messages_summary(self, obj):
        return (
            f"قبل: {obj.get_display_before_title()} | "
            f"بعد: {obj.get_display_after_title()} | "
            f"إجازة: {obj.get_display_holiday_title()}"
        )

    @admin.display(description="معاينة النصوص الحالية")
    def display_messages_preview(self, obj):
        if not obj:
            return "احفظ الإعدادات أولاً ثم عدّل النصوص من هنا."
        return format_html(
            "<div style='line-height:1.9'>"
            "<strong>قبل بداية الدوام:</strong> {} <span style='color:#6b7280'>(الشارة: {})</span><br>"
            "<strong>بعد انتهاء الدوام إذا كان الغد يومًا دراسيًا:</strong> {} <span style='color:#6b7280'>(الشارة: {})</span><br>"
            "<strong>بعد انتهاء الدوام إذا كان الغد إجازة:</strong> {} <span style='color:#6b7280'>(الشارة: {})</span><br>"
            "<strong>في يوم الإجازة:</strong> {} <span style='color:#6b7280'>(الشارة: {})</span>"
            "</div>",
            obj.get_display_before_title(),
            obj.get_display_before_badge(),
            obj.get_display_after_title(),
            obj.get_display_after_badge(),
            obj.get_display_after_holiday_title(),
            obj.get_display_after_holiday_badge(),
            obj.get_display_holiday_title(),
            obj.get_display_holiday_badge(),
        )


@admin.register(DutyAssignment)
class DutyAssignmentAdmin(admin.ModelAdmin):
    list_display = ("date", "school", "teacher_name", "duty_type", "location", "priority", "is_active")
    list_filter = ("date", "duty_type", "is_active")
    search_fields = ("teacher_name", "location", "school__name")
    autocomplete_fields = ("school",)
    ordering = ("-date", "priority", "-id")


# ============================================================
# Core Entities
# ============================================================

@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ("name", "settings")
    search_fields = ("name", "settings__name")
    autocomplete_fields = ("settings",)
    list_select_related = ("settings",)
    ordering = ("settings__name", "name")


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "school")
    search_fields = ("name", "school__name")
    autocomplete_fields = ("school",)
    list_select_related = ("school",)
    ordering = ("school__name", "name")


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("name", "school")
    search_fields = ("name", "school__name")
    autocomplete_fields = ("school",)
    list_select_related = ("school",)
    ordering = ("school__name", "name")


# ============================================================
# Day Schedule + Inlines
# ============================================================

class PeriodInline(admin.TabularInline):
    model = Period
    extra = 0
    fields = ("index", "starts_at", "ends_at", "school_class", "subject", "teacher")
    autocomplete_fields = ("school_class", "subject", "teacher")
    ordering = ("index", "starts_at")
    show_change_link = True


class BreakInline(admin.TabularInline):
    model = Break
    extra = 0
    fields = ("label", "starts_at", "duration_min")
    ordering = ("starts_at",)
    show_change_link = True


@admin.register(SchoolClosure)
class SchoolClosureAdmin(admin.ModelAdmin):
    list_display = ("title", "settings", "start_date", "end_date", "days_count", "is_active")
    list_filter = ("is_active", "start_date")
    search_fields = ("title", "settings__name")
    date_hierarchy = "start_date"
    ordering = ("-start_date",)


@admin.register(DaySchedule)
class DayScheduleAdmin(admin.ModelAdmin):
    list_display = ("settings", "weekday", "is_active", "periods_count")
    list_filter = ("weekday", "is_active")
    search_fields = ("settings__name", "settings__school__name")
    autocomplete_fields = ("settings",)
    list_select_related = ("settings",)
    ordering = ("settings__name", "weekday")
    inlines = [BreakInline, PeriodInline]


# ============================================================
# Period / Break
# ============================================================

@admin.register(Period)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ("day", "index", "starts_at", "ends_at", "school_class", "subject", "teacher")
    list_filter = ("day__weekday", "day__settings")
    search_fields = (
        "day__settings__name",
        "day__settings__school__name",
        "school_class__name",
        "subject__name",
        "teacher__name",
    )
    autocomplete_fields = ("day", "school_class", "subject", "teacher")
    list_select_related = ("day", "school_class", "subject", "teacher")
    ordering = ("day__weekday", "index", "starts_at")


@admin.register(Break)
class BreakAdmin(admin.ModelAdmin):
    list_display = ("day", "label", "starts_at", "duration_min")
    list_filter = ("day__weekday", "day__settings")
    search_fields = ("day__settings__name", "day__settings__school__name", "label")
    autocomplete_fields = ("day",)
    list_select_related = ("day",)
    ordering = ("day__weekday", "starts_at")


# ============================================================
# Class Lessons
# ============================================================

@admin.register(ClassLesson)
class ClassLessonAdmin(admin.ModelAdmin):
    list_display = ("settings", "weekday", "period_index", "school_class", "subject", "teacher", "is_active")
    list_filter = ("weekday", "is_active", "settings")
    search_fields = (
        "settings__name",
        "settings__school__name",
        "school_class__name",
        "subject__name",
        "teacher__name",
    )
    autocomplete_fields = ("settings", "school_class", "subject", "teacher")
    list_select_related = ("settings", "school_class", "subject", "teacher")
    ordering = ("settings__name", "weekday", "period_index", "school_class__name")
    
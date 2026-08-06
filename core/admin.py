# core/admin.py
from __future__ import annotations

from django.contrib import admin
from django.utils import timezone

from .models import School, DisplayScreen, ScreenOutage, UserProfile
from .screen_monitoring import SUPPRESSION_LABELS


class SchoolScopedAdmin(admin.ModelAdmin):
    """
    Mixin to restrict admin view to the user's school.
    Superusers see everything.
    """

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        profile = getattr(request.user, "profile", None)
        school = getattr(profile, "school", None)
        if not school:
            return qs.none()

        # Model has direct 'school'
        if hasattr(self.model, "school"):
            return qs.filter(school=school)

        # Model has 'settings' -> settings.school
        if hasattr(self.model, "settings"):
            return qs.filter(settings__school=school)

        return qs.none()

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            profile = getattr(request.user, "profile", None)
            school = getattr(profile, "school", None)
            if school and hasattr(obj, "school"):
                obj.school = school
        super().save_model(request, obj, form, change)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not request.user.is_superuser:
            profile = getattr(request.user, "profile", None)
            school = getattr(profile, "school", None)
            if school:
                if db_field.name == "school":
                    kwargs["queryset"] = School.objects.filter(id=school.id)
                elif db_field.name == "settings":
                    # For DaySchedule -> SchoolSettings (avoid circular import at module level)
                    from schedule.models import SchoolSettings
                    kwargs["queryset"] = SchoolSettings.objects.filter(school=school)

        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "school_type", "is_active", "created_at")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    list_filter = ("school_type", "is_active")
    ordering = ("name",)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "get_schools", "active_school")
    list_filter = ("schools", "active_school")
    search_fields = ("user__username", "user__email", "schools__name")
    autocomplete_fields = ("schools", "active_school")

    def get_schools(self, obj):
        return ", ".join([s.name for s in obj.schools.all()])
    get_schools.short_description = "المدارس المرتبطة"


@admin.register(DisplayScreen)
class DisplayScreenAdmin(SchoolScopedAdmin):
    """
    DisplayScreen admin:
    - token is read-only
    - supports last_seen_at OR last_seen (legacy) automatically
    """

    list_display = ("name", "school", "is_active", "last_seen_display")
    list_filter = ("school", "is_active")
    search_fields = ("name", "token")
    autocomplete_fields = ("school",)
    list_select_related = ("school",)

    def _has_field(self, field_name: str) -> bool:
        try:
            DisplayScreen._meta.get_field(field_name)
            return True
        except Exception:
            return False

    def get_readonly_fields(self, request, obj=None):
        ro = ["token"]
        # اجعل حقول آخر اتصال للقراءة فقط إن كانت موجودة
        if self._has_field("last_seen_at"):
            ro.append("last_seen_at")
        if self._has_field("last_seen"):
            ro.append("last_seen")
        if self._has_field("created_at"):
            ro.append("created_at")
        return tuple(ro)

    @admin.display(description="آخر اتصال")
    def last_seen_display(self, obj):
        if self._has_field("last_seen_at"):
            val = getattr(obj, "last_seen_at", None)
        else:
            val = getattr(obj, "last_seen", None)

        if not val:
            return "—"

        # عرض لطيف + آمن
        try:
            return timezone.localtime(val).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(val)


@admin.register(ScreenOutage)
class ScreenOutageAdmin(SchoolScopedAdmin):
    """Read-only outage log.

    Every outage is recorded, including the ones deliberately kept silent
    (after hours, inside a cooldown, during a platform incident). This view is
    where "why didn't I get a message?" gets an answer.
    """

    list_display = (
        "screen",
        "detected_at",
        "cause",
        "cause_confidence",
        "scope",
        "alert_state",
        "resolved_at",
    )
    list_filter = ("cause", "scope", "suppressed_reason", "screen__school")
    search_fields = ("screen__name", "cause_detail")
    list_select_related = ("screen", "screen__school")
    date_hierarchy = "detected_at"

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in ScreenOutage._meta.fields)

    @admin.display(description="حالة التنبيه")
    def alert_state(self, obj):
        if obj.alert_sent_at:
            return f"أُرسل ({obj.alert_count})"
        if obj.suppressed_reason:
            return SUPPRESSION_LABELS.get(obj.suppressed_reason, obj.suppressed_reason)
        return "—"

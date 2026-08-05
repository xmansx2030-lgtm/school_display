"""The weekly timetable: school days, periods, lessons and CSV export."""

from __future__ import annotations

from .view_helpers import *  # noqa: F401,F403  (shared view layer)


@manager_required
def days_list(request):
    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.warning(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    existing = set(
        DaySchedule.objects.filter(settings=settings_obj, weekday__in=SCHOOL_WEEKDAY_IDS)
        .values_list("weekday", flat=True)
    )

    for w in SCHOOL_WEEKDAY_IDS:
        if w not in existing:
            DaySchedule.objects.create(
                settings=settings_obj,
                weekday=w,
                periods_count=7 if w in (7, 1) else 6,
                is_active=(w not in WEEKEND_WEEKDAY_IDS),
            )

    days = list(
        DaySchedule.objects.filter(settings=settings_obj, weekday__in=SCHOOL_WEEKDAY_IDS)
        .prefetch_related("periods", "breaks")
    )
    days.sort(key=lambda d: WEEKDAY_SORT.get(d.weekday, 99))

    total_periods = 0
    active_days_count = 0
    holiday_days_count = 0
    for d in days:
        d.day_name = WEEKDAY_MAP.get(d.weekday, str(d.weekday))
        d.breaks_count = d.breaks.count()
        periods = sorted(d.periods.all(), key=lambda p: p.starts_at)
        if periods:
            d.first_period_time = periods[0].starts_at.strftime("%H:%M")
            d.last_period_time = periods[-1].ends_at.strftime("%H:%M")
            start_dt = datetime.combine(date.today(), periods[0].starts_at)
            end_dt = datetime.combine(date.today(), periods[-1].ends_at)
            diff = end_dt - start_dt
            hours, remainder = divmod(diff.seconds, 3600)
            minutes = remainder // 60
            d.total_duration = f"{hours:02d}:{minutes:02d}"
        else:
            d.first_period_time = "--:--"
            d.last_period_time = "--:--"
            d.total_duration = "--:--"
        if d.is_active:
            active_days_count += 1
            total_periods += d.periods_count or 0
        else:
            holiday_days_count += 1

    active_days = [d for d in days if d.is_active]
    avg_periods = total_periods / active_days_count if active_days_count else 0
    max_periods_day = max(active_days, key=lambda d: d.periods_count) if active_days else None
    min_periods_day = min(active_days, key=lambda d: d.periods_count) if active_days else None
    last_updated = timezone.localtime().strftime("%H:%M")
    avg_periods_display = f"{avg_periods:.1f}"

    return render(
        request,
        "dashboard/days_list.html",
        {
            "days": days,
            "total_periods": total_periods,
            "avg_periods": avg_periods,
            "avg_periods_display": avg_periods_display,
            "active_days_count": active_days_count,
            "holiday_days_count": holiday_days_count,
            "max_periods_day": max_periods_day,
            "min_periods_day": min_periods_day,
            "last_updated": last_updated,
        },
    )

@manager_required
@transaction.atomic
def day_edit(request, weekday: int):
    # Backward compatibility: older dashboards used Sunday=0
    if weekday == 0:
        weekday = 7
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "رقم اليوم غير صالح.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.warning(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day = get_object_or_404(DaySchedule, settings=settings_obj, weekday=weekday)
    day.day_name = WEEKDAY_MAP[weekday]

    if request.method == "POST":
        form = DayScheduleForm(request.POST, instance=day)
        p_formset = PeriodFormSet(request.POST, instance=day, prefix="p")
        b_formset = BreakFormSet(request.POST, instance=day, prefix="b")

        if form.is_valid() and p_formset.is_valid() and b_formset.is_valid():
            form.save()
            p_formset.save()
            b_formset.save()
            
            # ✅ Invalidate display cache + WS broadcast
            _invalidate_display_cache(school)
            
            messages.success(request, "تم حفظ جدول اليوم بنجاح.")
            return redirect("dashboard:days_list")

        detail = _collect_form_errors(form, p_formset, b_formset)
        if not detail:
            detail = "تحقق من الأوقات: يوجد حقول ناقصة/مكررة أو تداخلات زمنية."
        messages.error(request, detail)
    else:
        form = DayScheduleForm(instance=day)
        p_formset = PeriodFormSet(instance=day, prefix="p")
        b_formset = BreakFormSet(instance=day, prefix="b")

    return render(
        request,
        "dashboard/day_edit.html",
        {
            "day": day,
            "form": form,
            "p_formset": p_formset,
            "b_formset": b_formset,
            "autofill_seed": _build_day_autofill_seed(day),
        },
    )

@manager_required
@transaction.atomic
@require_POST
def day_autofill(request, weekday: int):
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "رقم اليوم غير صالح.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day = get_object_or_404(DaySchedule, settings=settings_obj, weekday=weekday)

    try:
        def _required_int_param(name: str, label: str, *, min_value: int) -> int:
            raw = (request.POST.get(name) or "").strip()
            if raw == "":
                raise ValueError(f"حقل '{label}' مطلوب.")
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"قيمة '{label}' غير صالحة.")
            if value < min_value:
                raise ValueError(f"قيمة '{label}' يجب أن تكون {min_value} أو أكثر.")
            return value

        start_time_str = (request.POST.get("start_time") or "").strip()
        if not start_time_str:
            raise ValueError("حقل 'بداية اليوم' مطلوب.")

        new_count = _required_int_param("target_periods_count", "عدد الحصص المطلوب", min_value=1)
        period_minutes = _required_int_param("period_minutes", "زمن الحصة", min_value=1)
        gap_minutes = _required_int_param("gap_minutes", "فاصل بين الحصص", min_value=0)
        break_after = _required_int_param("break_after", "الفسحة بعد حصة رقم", min_value=0)
        break_minutes = _required_int_param("break_minutes", "مدة الفسحة", min_value=0)

        day.periods_count = new_count
        day.save(update_fields=["periods_count"])

        period_seconds = 0
        gap_seconds = 0
        break_seconds = 0

        start_t = _parse_hhmm_or_hhmmss(start_time_str)

        p_len = timedelta(minutes=period_minutes, seconds=period_seconds)
        gap = timedelta(minutes=gap_minutes, seconds=gap_seconds)
        brk = timedelta(minutes=break_minutes, seconds=break_seconds)

        if p_len.total_seconds() <= 0:
            raise ValueError("طول الحصة يجب أن يكون أكبر من صفر.")

        if not day.periods_count or day.periods_count <= 0:
            messages.error(request, "عدد الحصص لليوم يساوي صفر.")
            return redirect("dashboard:day_edit", weekday=weekday)

        if break_after < 0 or break_after > day.periods_count:
            raise ValueError("قيمة 'الفسحة بعد الحصة رقم' خارج النطاق.")

        periods_mgr = _rev_manager(day, "periods", "period_set")
        breaks_mgr = _rev_manager(day, "breaks", "break_set")
        if periods_mgr is None or breaks_mgr is None:
            raise ValueError("تعذر الوصول لعلاقات الحصص/الفسح (تحقق من related_name).")

        base_date = timezone.localdate()
        cursor = datetime.combine(base_date, start_t)

        periods_mgr.all().delete()
        breaks_mgr.all().delete()

        break_minutes_final = (
            int(math.ceil(max(0, brk.total_seconds()) / 60.0))
            if brk.total_seconds() > 0
            else 0
        )

        for i in range(1, day.periods_count + 1):
            start_period = cursor
            end_period = cursor + p_len
            periods_mgr.create(index=i, starts_at=start_period.time(), ends_at=end_period.time())
            cursor = end_period

            if break_minutes_final > 0 and break_after == i:
                breaks_mgr.create(label="فسحة", starts_at=cursor.time(), duration_min=break_minutes_final)
                cursor += timedelta(minutes=break_minutes_final)

            cursor += gap

        # ✅ Invalidate display cache + WS broadcast
        _invalidate_display_cache(school)

        messages.success(request, "تمت التعبئة التلقائية للجدول.")
        return redirect("dashboard:day_edit", weekday=weekday)

    except Exception as e:
        logger.exception("day_autofill failed")
        messages.error(request, f"تعذّر تنفيذ التعبئة: {e}")
        return redirect("dashboard:day_edit", weekday=weekday)

@manager_required
@require_POST
def day_toggle(request, weekday: int):
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "اليوم غير صالح.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day, _ = DaySchedule.objects.get_or_create(settings=settings_obj, weekday=weekday)
    day.is_active = not bool(getattr(day, "is_active", True))
    day.save(update_fields=["is_active"])

    # ✅ Invalidate display cache + WS broadcast
    _invalidate_display_cache(school)

    status = "تفعيل" if day.is_active else "تعطيل"
    messages.success(request, f"تم {status} يوم {WEEKDAY_MAP.get(weekday, str(weekday))}.")
    return redirect("dashboard:days_list")

@manager_required
@transaction.atomic
@require_POST
def day_clear(request, weekday: int):
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "اليوم خارج أيام الأسبوع الدراسية.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.warning(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day = get_object_or_404(DaySchedule, settings=settings_obj, weekday=weekday)

    periods_mgr = _rev_manager(day, "periods", "period_set")
    breaks_mgr = _rev_manager(day, "breaks", "break_set")

    if periods_mgr is not None:
        periods_mgr.all().delete()
    if breaks_mgr is not None:
        breaks_mgr.all().delete()

    messages.success(request, "تم مسح جميع الحصص والفسح لهذا اليوم.")
    return redirect("dashboard:day_edit", weekday=weekday)

@manager_required
@transaction.atomic
@require_POST
def day_reindex(request, weekday: int):
    if weekday not in SCHOOL_WEEKDAY_IDS:
        messages.error(request, "اليوم خارج أيام الأسبوع الدراسية.")
        return redirect("dashboard:days_list")

    SchoolSettings = SchoolSettingsModel()
    DaySchedule = DayScheduleModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.warning(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:settings")

    day = get_object_or_404(DaySchedule, settings=settings_obj, weekday=weekday)

    periods_mgr = _rev_manager(day, "periods", "period_set")
    if periods_mgr is None:
        messages.error(request, "تعذر الوصول للحصص (تحقق من related_name).")
        return redirect("dashboard:day_edit", weekday=weekday)

    periods = list(periods_mgr.all())
    periods.sort(key=lambda p: (p.starts_at or time.min, p.ends_at or time.min))

    for i, p in enumerate(periods, start=1):
        if p.index != i:
            p.index = i
            p.save(update_fields=["index"])

    messages.success(request, "تمت إعادة ترقيم الحصص حسب الترتيب الزمني (١..ن).")
    return redirect("dashboard:day_edit", weekday=weekday)

@manager_required
def lessons_list(request):
    SchoolSettings = SchoolSettingsModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    lessons = ClassLesson.objects.none()
    if settings_obj:
        lessons = (
            ClassLesson.objects.filter(settings=settings_obj)
            .select_related("school_class", "subject", "teacher")
            .order_by("weekday", "period_index", "school_class__name")
        )

    search = (request.GET.get("search") or "").strip()
    day = (request.GET.get("day") or "").strip()

    if search:
        lessons = lessons.filter(
            Q(school_class__name__icontains=search)
            | Q(subject__name__icontains=search)
            | Q(teacher__name__icontains=search)
        )

    if day.isdigit():
        lessons = lessons.filter(weekday=int(day))

    return render(request, "dashboard/lessons_list.html", {"lessons": lessons})

@manager_required
def lesson_create(request):
    SchoolSettings = SchoolSettingsModel()
    Subject = SubjectModel()
    Teacher = TeacherModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if not settings_obj:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:lessons_list")

    form = LessonForm(request.POST or None)

    classes_qs = _classes_qs_from_settings(settings_obj).order_by("name")
    form.fields["school_class"].queryset = classes_qs
    form.fields["subject"].queryset = Subject.objects.filter(school=school).order_by("name")
    form.fields["teacher"].queryset = Teacher.objects.filter(school=school).order_by("name")

    if request.method == "POST":
        if form.is_valid():
            lesson = form.save(commit=False)
            lesson.settings = settings_obj
            lesson.save()
            _invalidate_display_cache(school)
            messages.success(request, "تمت إضافة الحصة بنجاح.")
            return redirect("dashboard:lessons_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")

    return render(request, "dashboard/lesson_form.html", {"form": form, "title": "إضافة حصة"})

@manager_required
def lesson_edit(request, pk: int):
    SchoolSettings = SchoolSettingsModel()
    ClassLesson = ClassLessonModel()
    Subject = SubjectModel()
    Teacher = TeacherModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:lessons_list")

    obj = get_object_or_404(ClassLesson, pk=pk, settings=settings_obj)

    form = LessonForm(request.POST or None, instance=obj)

    classes_qs = _classes_qs_from_settings(settings_obj).order_by("name")
    form.fields["school_class"].queryset = classes_qs
    form.fields["subject"].queryset = Subject.objects.filter(school=school).order_by("name")
    form.fields["teacher"].queryset = Teacher.objects.filter(school=school).order_by("name")

    if request.method == "POST":
        if form.is_valid():
            form.save()
            _invalidate_display_cache(school)
            messages.success(request, "تم تعديل الحصة بنجاح.")
            return redirect("dashboard:lessons_list")
        messages.error(request, "الرجاء تصحيح الأخطاء.")

    return render(request, "dashboard/lesson_form.html", {"form": form, "title": "تعديل حصة"})

@manager_required
@require_POST
def lesson_delete(request, pk: int):
    SchoolSettings = SchoolSettingsModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "فضلاً أضف إعدادات المدرسة أولاً.")
        return redirect("dashboard:lessons_list")

    obj = get_object_or_404(ClassLesson, pk=pk, settings=settings_obj)
    obj.delete()
    _invalidate_display_cache(school)
    messages.success(request, "تم حذف الحصة.")
    return redirect("dashboard:lessons_list")

@manager_required
def timetable_day_view(request):
    SchoolSettings = SchoolSettingsModel()
    Period = PeriodModel()
    Subject = SubjectModel()
    Teacher = TeacherModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "يجب أولاً إعداد إعدادات المدرسة والجدول الزمني.")
        return redirect("dashboard:settings")

    weekdays_choices = list(SCHOOL_WEEK)
    default_weekday = weekdays_choices[0][0] if weekdays_choices else 0

    weekday_param = request.GET.get("weekday") if request.method == "GET" else request.POST.get("weekday")
    class_param = request.GET.get("class_id") if request.method == "GET" else request.POST.get("class_id")

    try:
        weekday = int(weekday_param) if weekday_param not in (None, "") else int(default_weekday)
    except (TypeError, ValueError):
        weekday = int(default_weekday)

    classes_qs = _classes_qs_from_settings(settings_obj).order_by("name")

    selected_class = None
    if classes_qs.exists():
        try:
            if class_param not in (None, ""):
                selected_class = classes_qs.get(pk=int(class_param))
            else:
                selected_class = classes_qs.first()
        except (ValueError, classes_qs.model.DoesNotExist):
            selected_class = classes_qs.first()

    selected_class_id = selected_class.id if selected_class else None

    periods_qs = Period.objects.filter(day__settings=settings_obj, day__weekday=weekday).order_by("index")
    subjects_qs = Subject.objects.filter(school=school).order_by("name")
    teachers_qs = Teacher.objects.filter(school=school).order_by("name")

    if selected_class is not None:
        existing_lessons_qs = ClassLesson.objects.filter(
            settings=settings_obj,
            weekday=weekday,
            school_class=selected_class,
        ).select_related("subject", "teacher")
    else:
        existing_lessons_qs = ClassLesson.objects.none()

    lessons_map: dict[int, object] = {l.period_index: l for l in existing_lessons_qs}

    if request.method == "POST" and selected_class is not None:
        created_count = 0
        updated_count = 0
        deleted_count = 0

        subjects_by_id = {s.id: s for s in subjects_qs}
        teachers_by_id = {t.id: t for t in teachers_qs}

        with transaction.atomic():
            for period in periods_qs:
                period_index = period.index
                existing_lesson = lessons_map.get(period_index)

                subject_field = f"subject-{selected_class.id}-{period_index}"
                teacher_field = f"teacher-{selected_class.id}-{period_index}"

                subject_raw = (request.POST.get(subject_field) or "").strip()
                teacher_raw = (request.POST.get(teacher_field) or "").strip()

                subject_obj = None
                teacher_obj = None

                if subject_raw:
                    try:
                        subject_obj = subjects_by_id.get(int(subject_raw))
                    except (TypeError, ValueError):
                        subject_obj = None

                if teacher_raw:
                    try:
                        teacher_obj = teachers_by_id.get(int(teacher_raw))
                    except (TypeError, ValueError):
                        teacher_obj = None

                if subject_obj is None and teacher_obj is None:
                    if existing_lesson is not None:
                        existing_lesson.delete()
                        deleted_count += 1
                    continue

                if existing_lesson is not None:
                    if subject_obj is None:
                        subject_obj = existing_lesson.subject
                    if teacher_obj is None:
                        teacher_obj = existing_lesson.teacher

                if subject_obj is None or teacher_obj is None:
                    continue

                if existing_lesson is None:
                    ClassLesson.objects.create(
                        settings=settings_obj,
                        school_class=selected_class,
                        weekday=weekday,
                        period_index=period_index,
                        subject=subject_obj,
                        teacher=teacher_obj,
                        is_active=True,
                    )
                    created_count += 1
                else:
                    changed = False
                    if existing_lesson.subject_id != subject_obj.id:
                        existing_lesson.subject = subject_obj
                        changed = True
                    if existing_lesson.teacher_id != teacher_obj.id:
                        existing_lesson.teacher = teacher_obj
                        changed = True
                    if not existing_lesson.is_active:
                        existing_lesson.is_active = True
                        changed = True
                    if changed:
                        existing_lesson.save()
                        updated_count += 1

        if created_count or updated_count or deleted_count:
            msg_parts = []
            if created_count:
                msg_parts.append(f"تم إنشاء {created_count} حصة جديدة.")
            if updated_count:
                msg_parts.append(f"تم تحديث {updated_count} حصة.")
            if deleted_count:
                msg_parts.append(f"تم حذف {deleted_count} حصة فارغة.")
            messages.success(request, " ".join(msg_parts))
        else:
            messages.info(request, "لم يتم رصد أي تغييرات في جدول هذا الفصل.")

        url = reverse("dashboard:timetable_day")
        url = f"{url}?weekday={weekday}&class_id={selected_class.id}"
        return redirect(url)

    rows: list[dict] = []
    for period in periods_qs:
        lesson = lessons_map.get(period.index)
        rows.append(
            {
                "period": period,
                "subject_id": lesson.subject_id if lesson else None,
                "teacher_id": lesson.teacher_id if lesson else None,
            }
        )

    return render(
        request,
        "dashboard/timetable_day.html",
        {
            "school": school,
            "settings": settings_obj,
            "weekdays": weekdays_choices,
            "weekday": weekday,
            "classes": classes_qs,
            "selected_class": selected_class,
            "selected_class_id": selected_class_id,
            "periods": periods_qs,
            "rows": rows,
            "subjects": subjects_qs,
            "teachers": teachers_qs,
        },
    )

@manager_required
def timetable_week_view(request):
    SchoolSettings = SchoolSettingsModel()
    Period = PeriodModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "يجب أولاً إعداد إعدادات المدرسة والجدول الزمني.")
        return redirect("dashboard:settings")

    class_param = request.GET.get("class_id")
    classes_qs = _classes_qs_from_settings(settings_obj).order_by("name")

    if not classes_qs.exists():
        messages.error(request, "لا توجد فصول مسجلة لهذه المدرسة.")
        return redirect("dashboard:timetable_day")

    try:
        selected_class = classes_qs.get(pk=int(class_param)) if class_param else classes_qs.first()
    except (ValueError, classes_qs.model.DoesNotExist):
        selected_class = classes_qs.first()

    all_periods_qs = Period.objects.filter(
        day__settings=settings_obj, day__weekday__in=SCHOOL_WEEKDAY_IDS
    ).order_by("day__weekday", "index")

    periods_by_weekday: dict[int, list] = {}
    for p in all_periods_qs:
        periods_by_weekday.setdefault(p.day.weekday, []).append(p)

    all_lessons_qs = ClassLesson.objects.filter(
        settings=settings_obj,
        school_class=selected_class,
        weekday__in=SCHOOL_WEEKDAY_IDS,
    ).select_related("subject", "teacher")

    lessons_by_weekday_period: dict[int, dict[int, object]] = {}
    for lesson in all_lessons_qs:
        lessons_by_weekday_period.setdefault(lesson.weekday, {})[lesson.period_index] = lesson

    days_data = []
    for weekday, label in SCHOOL_WEEK:
        rows = []
        for period in periods_by_weekday.get(weekday, []):
            lesson = lessons_by_weekday_period.get(weekday, {}).get(period.index)
            rows.append(
                {
                    "period": period,
                    "lesson": lesson,
                    "subject_name": lesson.subject.name if lesson and lesson.subject else "",
                    "teacher_name": lesson.teacher.name if lesson and lesson.teacher else "",
                }
            )
        days_data.append({"weekday": weekday, "label": label, "rows": rows})

    return render(
        request,
        "dashboard/timetable_week.html",
        {"school": school, "settings": settings_obj, "selected_class": selected_class, "days_data": days_data},
    )

@manager_required
def timetable_export_csv(request):
    SchoolSettings = SchoolSettingsModel()
    Period = PeriodModel()
    ClassLesson = ClassLessonModel()

    school, response = get_active_school_or_redirect(request)
    if response:
        return response

    settings_obj = SchoolSettings.objects.filter(school=school).first()
    if settings_obj is None:
        messages.error(request, "يجب أولاً إعداد إعدادات المدرسة والجدول الزمني.")
        return redirect("dashboard:settings")

    class_param = request.GET.get("class_id")
    if not class_param:
        messages.error(request, "لم يتم تحديد الفصل المطلوب للتصدير.")
        return redirect("dashboard:timetable_day")

    classes_qs = _classes_qs_from_settings(settings_obj)
    try:
        school_class = classes_qs.get(pk=int(class_param))
    except (ValueError, classes_qs.model.DoesNotExist):
        messages.error(request, "الفصل المحدد غير موجود.")
        return redirect("dashboard:timetable_day")

    periods_qs = Period.objects.filter(day__settings=settings_obj).select_related("day")
    period_map: dict[tuple[int, int], object] = {(p.day.weekday, p.index): p for p in periods_qs}

    lessons = (
        ClassLesson.objects.filter(settings=settings_obj, school_class=school_class)
        .select_related("subject", "teacher")
        .order_by("weekday", "period_index")
    )

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"timetable_{school_class.name}.csv"
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.write("\ufeff")

    writer = csv.writer(resp)
    writer.writerow(["اليوم", "رقم الحصة", "وقت البداية", "وقت النهاية", "المادة", "المعلم/ـة"])

    for lesson in lessons:
        period = period_map.get((lesson.weekday, lesson.period_index))
        day_label = WEEKDAY_MAP.get(lesson.weekday, str(lesson.weekday))
        start_str = period.starts_at.strftime("%H:%M") if period and period.starts_at else ""
        end_str = period.ends_at.strftime("%H:%M") if period and period.ends_at else ""
        subject_name = lesson.subject.name if lesson.subject else ""
        teacher_name = lesson.teacher.name if lesson.teacher else ""
        writer.writerow([day_label, lesson.period_index, start_str, end_str, subject_name, teacher_name])

    return resp

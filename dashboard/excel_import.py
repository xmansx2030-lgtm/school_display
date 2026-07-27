from __future__ import annotations

from datetime import datetime, time
from io import BytesIO

from django.db import transaction
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from schedule.models import (
    ClassLesson,
    DaySchedule,
    Period,
    SchoolClass,
    SchoolSettings,
    Subject,
    Teacher,
)


DAY_NAMES = {
    "الاثنين": 1,
    "الثلاثاء": 2,
    "الأربعاء": 3,
    "الخميس": 4,
    "الجمعة": 5,
    "السبت": 6,
    "الأحد": 7,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
    "5": 5,
    "6": 6,
    "7": 7,
}
DAY_LABELS = {value: key for key, value in DAY_NAMES.items() if not key.isdigit()}
MAX_IMPORT_ROWS = 5000


def _style_sheet(ws):
    ws.sheet_view.rightToLeft = True
    fill = PatternFill("solid", fgColor="1D4ED8")
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"


def build_template_bytes() -> bytes:
    wb = Workbook()
    teachers = wb.active
    teachers.title = "المعلمون"
    teachers.append(["اسم المعلم"])
    teachers.append(["أحمد محمد"])
    classes = wb.create_sheet("الفصول")
    classes.append(["اسم الفصل"])
    classes.append(["الأول أ"])
    subjects = wb.create_sheet("المواد")
    subjects.append(["اسم المادة"])
    subjects.append(["الرياضيات"])
    timetable = wb.create_sheet("الجدول")
    timetable.append(
        ["اليوم", "رقم الحصة", "وقت البداية", "وقت النهاية", "الفصل", "المادة", "المعلم"]
    )
    timetable.append(["الأحد", 1, "07:00", "07:45", "الأول أ", "الرياضيات", "أحمد محمد"])
    instructions = wb.create_sheet("تعليمات")
    instructions.append(["تعليمات الاستيراد"])
    instructions.append(["لا تغيّر أسماء الأوراق أو عناوين الأعمدة."])
    instructions.append(["صيغة الوقت المقبولة 07:00 أو 07:00:00."])
    instructions.append(["يمكن تكرار اليوم ورقم الحصة لفصول مختلفة بشرط تطابق الوقت."])
    instructions.append(["راجع الأخطاء في شاشة المعاينة قبل تنفيذ الاستيراد."])
    for ws in wb.worksheets:
        _style_sheet(ws)
        for column in ws.columns:
            width = min(max(len(str(cell.value or "")) for cell in column) + 4, 42)
            ws.column_dimensions[column[0].column_letter].width = width
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def _text(value) -> str:
    return str(value or "").strip()


def _parse_time(value) -> time | None:
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    raw = _text(value)
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def _names_from_sheet(wb, sheet_name: str) -> tuple[list[str], bool]:
    if sheet_name not in wb.sheetnames:
        return [], False
    result, seen = [], set()
    exceeded = False
    for row_number, row in enumerate(
        wb[sheet_name].iter_rows(min_row=2, values_only=True),
        start=2,
    ):
        if row_number > MAX_IMPORT_ROWS + 1:
            exceeded = True
            break
        name = _text(row[0] if row else "")
        if name and name.casefold() not in seen:
            result.append(name)
            seen.add(name.casefold())
    return result, exceeded


def parse_workbook(uploaded_file) -> dict:
    try:
        wb = load_workbook(uploaded_file, read_only=True, data_only=True)
    except Exception as exc:
        return {"rows": [], "errors": [f"تعذر قراءة ملف Excel: {exc}"], "entities": {}}
    if "الجدول" not in wb.sheetnames:
        return {"rows": [], "errors": ["ورقة «الجدول» غير موجودة. استخدم القالب المعتمد."], "entities": {}}

    teachers, teachers_exceeded = _names_from_sheet(wb, "المعلمون")
    classes, classes_exceeded = _names_from_sheet(wb, "الفصول")
    subjects, subjects_exceeded = _names_from_sheet(wb, "المواد")
    entities = {"teachers": teachers, "classes": classes, "subjects": subjects}
    rows, errors, seen_slots, slot_times = [], [], set(), {}
    for label, exceeded in (
        ("المعلمون", teachers_exceeded),
        ("الفصول", classes_exceeded),
        ("المواد", subjects_exceeded),
    ):
        if exceeded:
            errors.append(f"ورقة «{label}» تجاوزت الحد الأقصى ({MAX_IMPORT_ROWS} صف).")
    for row_number, values in enumerate(wb["الجدول"].iter_rows(min_row=2, values_only=True), start=2):
        if row_number > MAX_IMPORT_ROWS + 1:
            errors.append(f"تجاوز الملف الحد الأقصى ({MAX_IMPORT_ROWS} صف).")
            break
        padded = tuple(values or ()) + (None,) * max(0, 7 - len(tuple(values or ())))
        day_raw, index_raw, start_raw, end_raw, class_raw, subject_raw, teacher_raw = padded[:7]
        if not any(_text(value) for value in padded[:7]):
            continue
        row_errors = []
        day = DAY_NAMES.get(_text(day_raw))
        try:
            period_index = int(index_raw)
        except (TypeError, ValueError):
            period_index = 0
        starts_at, ends_at = _parse_time(start_raw), _parse_time(end_raw)
        class_name, subject_name, teacher_name = _text(class_raw), _text(subject_raw), _text(teacher_raw)
        if day is None:
            row_errors.append("اسم اليوم غير صحيح")
        if period_index < 1 or period_index > 20:
            row_errors.append("رقم الحصة يجب أن يكون بين 1 و20")
        if starts_at is None:
            row_errors.append("وقت البداية غير صحيح")
        if ends_at is None:
            row_errors.append("وقت النهاية غير صحيح")
        if starts_at and ends_at and starts_at >= ends_at:
            row_errors.append("وقت النهاية يجب أن يكون بعد البداية")
        if not class_name:
            row_errors.append("الفصل مطلوب")
        if not subject_name:
            row_errors.append("المادة مطلوبة")
        if not teacher_name:
            row_errors.append("المعلم مطلوب")
        if day and period_index and starts_at and ends_at:
            slot = (day, period_index)
            previous = slot_times.get(slot)
            if previous and previous != (starts_at, ends_at):
                row_errors.append("وقت الحصة لا يطابق الصفوف الأخرى لنفس اليوم ورقم الحصة")
            else:
                slot_times[slot] = (starts_at, ends_at)
            lesson_slot = (day, period_index, class_name.casefold())
            if lesson_slot in seen_slots:
                row_errors.append("الحصة مكررة لنفس الفصل")
            seen_slots.add(lesson_slot)
        normalized = {
            "row": row_number,
            "day": day,
            "day_label": DAY_LABELS.get(day, _text(day_raw)),
            "period_index": period_index,
            "starts_at": starts_at.strftime("%H:%M") if starts_at else "",
            "ends_at": ends_at.strftime("%H:%M") if ends_at else "",
            "class_name": class_name,
            "subject_name": subject_name,
            "teacher_name": teacher_name,
            "errors": row_errors,
        }
        rows.append(normalized)
        errors.extend(f"الصف {row_number}: {message}" for message in row_errors)
    if not rows:
        errors.append("ورقة الجدول لا تحتوي أي حصص.")
    return {"rows": rows, "errors": errors, "entities": entities}


@transaction.atomic
def apply_import(*, school, parsed: dict) -> dict:
    if parsed.get("errors"):
        raise ValueError("لا يمكن استيراد ملف يحتوي أخطاء.")
    settings_obj, _ = SchoolSettings.objects.get_or_create(school=school, defaults={"name": school.name})
    entities = parsed.get("entities") or {}
    for name in entities.get("teachers", []):
        Teacher.objects.get_or_create(school=school, name=name)
    for name in entities.get("subjects", []):
        Subject.objects.get_or_create(school=school, name=name)
    for name in entities.get("classes", []):
        SchoolClass.objects.get_or_create(settings=settings_obj, name=name)

    period_slots, imported_lessons = {}, 0
    for row in parsed["rows"]:
        teacher, _ = Teacher.objects.get_or_create(school=school, name=row["teacher_name"])
        subject, _ = Subject.objects.get_or_create(school=school, name=row["subject_name"])
        school_class, _ = SchoolClass.objects.get_or_create(settings=settings_obj, name=row["class_name"])
        day, _ = DaySchedule.objects.get_or_create(
            settings=settings_obj,
            weekday=row["day"],
            defaults={"is_active": True, "periods_count": row["period_index"]},
        )
        if not day.is_active or day.periods_count < row["period_index"]:
            day.is_active = True
            day.periods_count = max(day.periods_count, row["period_index"])
            day.save(update_fields=("is_active", "periods_count"))
        slot = (row["day"], row["period_index"])
        if slot not in period_slots:
            Period.objects.update_or_create(
                day=day,
                index=row["period_index"],
                defaults={
                    "starts_at": datetime.strptime(row["starts_at"], "%H:%M").time(),
                    "ends_at": datetime.strptime(row["ends_at"], "%H:%M").time(),
                },
            )
            period_slots[slot] = True
        ClassLesson.objects.update_or_create(
            settings=settings_obj,
            weekday=row["day"],
            period_index=row["period_index"],
            school_class=school_class,
            defaults={"subject": subject, "teacher": teacher, "is_active": True},
        )
        imported_lessons += 1
    return {
        "lessons": imported_lessons,
        "teachers": Teacher.objects.filter(school=school).count(),
        "subjects": Subject.objects.filter(school=school).count(),
        "classes": SchoolClass.objects.filter(settings=settings_obj).count(),
    }

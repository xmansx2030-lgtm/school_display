"""هل المدرسة مغلقة في هذا التاريخ؟

الجداول تعرف أيام الأسبوع ولا تعرف التقويم، فبقي سؤالٌ واحد بلا جواب: **هل
اليوم إجازة؟** وغيابه كان يظهر في مكانين لا واحد — الشاشة تعرض جدولاً في يوم
إجازة، والمراقب يرسل تنبيه انقطاع عن شاشةٍ أُطفئت عمداً.

هذه الوحدة هي الجواب الوحيد لذلك السؤال. كل من يحتاجه يسأل هنا، فلا يتفرّق
التقويم على ثلاث نسخ تختلف.

**لماذا الكاش هنا.** المراقب يمسح كل دقيقة، ومحرك العرض يُستدعى لكل شاشة عند
كل استطلاع. استعلامٌ لكل نداء يعني آلاف الاستعلامات يومياً عن جدولٍ يتغيّر
مرتين في الفصل. المفتاح يحمل ``schedule_revision``، والإشارة ترفعه عند أي
تعديل على الإجازات — فالكاش يسقط من نفسه بلا مسحٍ صريح.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.core.cache import cache


# نافذة البحث عن «اليوم الدراسي التالي» أسبوعان، فنقرأ مدىً يغطيها دفعة واحدة.
LOOKAHEAD_DAYS = 21
CACHE_TTL_SECONDS = 6 * 60 * 60


def _scope(school_settings) -> str:
    """معرّف صفّ الإعدادات نفسه، لا معرّف مدرسته.

    ``school_id`` يصير صفراً لكل صفّ إعدادات بلا مدرسة، فيتقاسم جميعها دلواً
    واحداً في الكاش وتقرأ إجازات بعضها. المفتاح الأساسي فريدٌ دائماً، فهو
    وحده ما يصلح مدىً للكاش.
    """
    pk = getattr(school_settings, "pk", None)
    if pk:
        return f"s{int(pk)}"
    return f"school{int(getattr(school_settings, 'school_id', 0) or 0)}"


def _revision(school_settings) -> int:
    return int(getattr(school_settings, "schedule_revision", 0) or 0)


def _cache_key(school_settings, *, start: date, days: int) -> str:
    return (
        f"schedule:closures:{_scope(school_settings)}"
        f":r{_revision(school_settings)}:{start.isoformat()}:{int(days)}"
    )


def _query_ranges(school_settings, *, start: date, end: date) -> list[tuple[date, date, str]]:
    manager = getattr(school_settings, "closures", None)
    if manager is None or not hasattr(manager, "filter"):
        return []
    rows = manager.filter(
        is_active=True,
        start_date__lte=end,
        end_date__gte=start,
    ).values_list("start_date", "end_date", "title")
    return [(row[0], row[1], row[2] or "") for row in rows]


def closure_titles(school_settings, *, start: date, days: int = LOOKAHEAD_DAYS) -> dict[str, str]:
    """خريطة ``ISO-date -> اسم المناسبة`` لكل يوم مغلق داخل المدى.

    تُعاد التواريخ نصوصاً لأنها تُخزَّن في الكاش، والنص يعبُر أي واجهة تخزين
    بلا اعتماد على فكّ تسلسل خاص.
    """
    days = max(1, int(days))
    key = _cache_key(school_settings, start=start, days=days)
    try:
        cached = cache.get(key)
        if isinstance(cached, dict):
            return cached
    except Exception:
        pass

    end = start + timedelta(days=days - 1)
    mapping: dict[str, str] = {}
    for range_start, range_end, title in _query_ranges(school_settings, start=start, end=end):
        if not range_start or not range_end:
            continue
        cursor = max(range_start, start)
        last = min(range_end, end)
        while cursor <= last:
            # أول مناسبة تغطي اليوم تكفي: التداخل بين إجازتين لا يغيّر الحكم.
            mapping.setdefault(cursor.isoformat(), title)
            cursor += timedelta(days=1)

    try:
        cache.set(key, mapping, timeout=CACHE_TTL_SECONDS)
    except Exception:
        pass
    return mapping


def closed_dates(school_settings, *, start: date, days: int = LOOKAHEAD_DAYS) -> set[date]:
    return {
        date.fromisoformat(iso)
        for iso in closure_titles(school_settings, start=start, days=days)
    }


def closure_title_on(school_settings, day: date) -> str | None:
    """اسم المناسبة إن كان اليوم مغلقاً، وإلا ``None``.

    النص الفارغ حالة قائمة (إجازة بلا عنوان)، فيُميَّز عن الغياب بإرجاع سلسلة
    فارغة لا ``None`` — والفحص يكون ``is not None`` لا الصدق.
    """
    mapping = closure_titles(school_settings, start=day, days=1)
    return mapping.get(day.isoformat())


def is_closed(school_settings, day: date) -> bool:
    return closure_title_on(school_settings, day) is not None

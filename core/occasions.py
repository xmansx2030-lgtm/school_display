"""سجل المناسبات: مصدر الحقيقة الوحيد لكل ما يخص قوالب المناسبات.

قبل هذا الملف كانت المناسبة معرّفة في خمسة أماكن — نص اللوحة، وبيانات
JavaScript، وألوان CSS، وقائمتا اختيارات في موديلين — فكان إضافة مناسبة واحدة
يتطلب خمسة تعديلات، ونسيان أحدها يظهر كخلل صامت: قالب يُختار من اللوحة ولا
يظهر له ثيم على الشاشة، أو معاينة بلون يخالف ما يراه الطلاب فعلًا.

الآن كل شيء هنا:
    • الهوية النصية   → عنوان ونص التنبيه الافتراضيين
    • الهوية البصرية  → الألوان والرموز والنمط الزخرفي وشكل الإطار
    • الجدولة         → تاريخ المناسبة ميلاديًا أو هجريًا

وتُشتق منه: اختيارات الموديلات، وبطاقات لوحة المدير، و``:root`` في صفحة
العرض، وبيانات ``display.js``.

هذا الملف لا يستورد أي موديل، فيبقى آمنًا للاستيراد من ``core`` و ``notices``
و ``dashboard`` معًا بلا دوران في الاستيراد.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Literal, Optional

import logging


logger = logging.getLogger(__name__)


# محدّد التنويع U+FE0E يفرض العرض النصّي الأحادي على الرموز التي يملك بعضها
# نسخة إيموجي ملوّنة. بدونه قد يظهر ⚔ رماديًا على ويندوز وملوّنًا على أندرويد،
# فتختلف هوية المناسبة باختلاف الجهاز.
_TEXT_PRESENTATION = "︎"


def _glyph(symbol: str) -> str:
    """رمز أحادي اللون يرث لون الثيم على كل الأجهزة."""
    return symbol + _TEXT_PRESENTATION


# نافذة الاقتراح: كم يومًا قبل المناسبة نذكّر المدير بها في لوحة التحكم.
SUGGESTION_LEAD_DAYS = 10


@dataclass(frozen=True)
class OccasionSchedule:
    """موعد المناسبة.

    ``calendar="gregorian"`` لتواريخ ثابتة كاليوم الوطني (٢٣ سبتمبر)،
    و ``calendar="hijri"`` لما يتبع التقويم الهجري كرمضان والعيدين.
    """

    calendar: Literal["gregorian", "hijri"]
    month: int
    day: int

    def next_occurrence(self, today: date) -> Optional[date]:
        """أقرب وقوع للمناسبة في اليوم الحالي أو بعده."""
        if self.calendar == "gregorian":
            return self._next_gregorian(today)
        return self._next_hijri(today)

    def _next_gregorian(self, today: date) -> Optional[date]:
        for year in (today.year, today.year + 1):
            try:
                candidate = date(year, self.month, self.day)
            except ValueError:
                continue
            if candidate >= today:
                return candidate
        return None

    def _next_hijri(self, today: date) -> Optional[date]:
        try:
            from hijridate import Gregorian, Hijri
        except Exception:
            logger.warning("hijridate unavailable; hijri occasions will not be suggested")
            return None

        try:
            current_hijri_year = Gregorian(today.year, today.month, today.day).to_hijri().year
        except Exception:
            logger.exception("hijri_conversion_failed date=%s", today)
            return None

        # سنتان تكفيان: المكتبة محدودة المدى، والوقوع القادم لا يتجاوز سنة.
        for hijri_year in (current_hijri_year, current_hijri_year + 1):
            try:
                candidate = Hijri(hijri_year, self.month, self.day).to_gregorian()
            except (ValueError, OverflowError):
                continue
            candidate_date = date(candidate.year, candidate.month, candidate.day)
            if candidate_date >= today:
                return candidate_date
        return None


@dataclass(frozen=True)
class Occasion:
    """مناسبة واحدة بكل هويتها النصية والبصرية وموعدها."""

    key: str
    name: str
    theme_name: str
    title: str
    body: str
    level: str
    duration_hours: int

    # ── الهوية البصرية ──────────────────────────────────────────────────
    # ``mark`` هو الرمز الكبير في بطل الشاشة، و ``badge_icon`` أيقونة الشارة
    # الصغيرة أثناء الحصص. الفصل بينهما مقصود: رمز يليق بحجم ٩rem قد لا يقرأ
    # في شارة بحجم ١rem.
    mark: str
    badge_icon: str
    symbols: tuple[str, str]
    tagline: str
    deep: str
    accent: str
    highlight: str
    soft: str
    pattern_css: str
    mark_shape: str = "50%"
    mark_transform: str = "none"
    # الرمز الكبير نصّي (رقم أو حرف) لا إيموجي، فيحتاج خطًا ولونًا لا حجمًا فقط.
    mark_is_text: bool = False

    schedule: Optional[OccasionSchedule] = None
    aliases: tuple[str, ...] = field(default=())

    # ── مشتقات للاستهلاك المباشر ────────────────────────────────────────

    def as_theme_dict(self) -> dict:
        """الشكل الذي تقرأه ``display.js`` لبناء الشارة والبطل."""
        return {
            "key": self.key,
            "label": self.name,
            "mark": self.mark,
            "markIsText": self.mark_is_text,
            "badgeIcon": self.badge_icon,
            "symbols": list(self.symbols),
            "tagline": self.tagline,
        }

    def as_card_dict(self) -> dict:
        """الشكل الذي تعرضه بطاقة لوحة المدير — بنفس ألوان الشاشة ورموزها."""
        return {
            "key": self.key,
            "name": self.name,
            "theme_name": self.theme_name,
            "title": self.title,
            "body": self.body,
            "mark": self.mark,
            "mark_is_text": self.mark_is_text,
            "badge_icon": self.badge_icon,
            "tagline": self.tagline,
            "deep": self.deep,
            "accent": self.accent,
            "highlight": self.highlight,
            "soft": self.soft,
            "pattern_css": self.pattern_css,
            "mark_shape": self.mark_shape,
            "mark_transform": self.mark_transform,
            "duration_hours": self.duration_hours,
        }

    def as_template_dict(self) -> dict:
        """القيم التي يُملأ بها نموذج إنشاء التنبيه."""
        return {
            "title": self.title,
            "body": self.body,
            "level": self.level,
            "theme": self.key,
            "duration_hours": self.duration_hours,
        }


# ═══════════════════════════════════════════════════════════════════════════
# السجل
#
# الألوان مأخوذة من هوية الشاشة الفعلية (لا من بطاقات اللوحة القديمة التي
# كانت تعرض قيمًا مختلفة). الرموز نصّية أو هندسية حيثما كان ذلك أرقى من
# الإيموجي: ⚔ إشارة إلى سيفَي الشعار الوطني، و١٧٢٧ سنة تأسيس الدولة.
# ═══════════════════════════════════════════════════════════════════════════

_OCCASION_LIST: tuple[Occasion, ...] = (
    Occasion(
        key="national_day",
        name="اليوم الوطني السعودي",
        theme_name="أخضر الوطن",
        title="دام عزك يا وطن",
        body="نحتفي اليوم بوطننا الغالي، ونسأل الله أن يديم على المملكة أمنها وعزها وازدهارها.",
        level="success",
        duration_hours=24,
        mark=_glyph("⚔"),
        badge_icon=_glyph("⚔"),
        symbols=(_glyph("⚔"), _glyph("✦")),
        tagline="هوية وطن • فخر وانتماء",
        deep="#001d16",
        accent="#009b4d",
        highlight="#e2c269",
        soft="#d1fae5",
        pattern_css=(
            "linear-gradient(30deg, var(--occasion-highlight) 12%, transparent 12.5%, "
            "transparent 87%, var(--occasion-highlight) 87.5%)"
        ),
        schedule=OccasionSchedule("gregorian", 9, 23),
    ),
    Occasion(
        key="founding_day",
        name="يوم التأسيس",
        theme_name="الهوية النجدية",
        title="يوم بدينا",
        body="نحتفي بذكرى تأسيس الدولة السعودية، وبجذور راسخة تمتد لأكثر من ثلاثة قرون.",
        level="success",
        duration_hours=24,
        mark="١٧٢٧",
        badge_icon=_glyph("◈"),
        symbols=(_glyph("◈"), "١٧٢٧"),
        tagline="جذور راسخة • إرث ممتد",
        deep="#28170f",
        accent="#9a623b",
        highlight="#e0b783",
        soft="#ffedd5",
        pattern_css="repeating-linear-gradient(45deg, transparent 0 22px, var(--occasion-highlight) 23px 25px)",
        mark_shape="32% 68% 42% 58% / 58% 38% 62% 42%",
        mark_is_text=True,
        schedule=OccasionSchedule("gregorian", 2, 22),
    ),
    Occasion(
        key="flag_day",
        name="يوم العلم السعودي",
        theme_name="راية التوحيد",
        title="رايتنا عهد وميثاق",
        body="نستذكر اليوم رمز وحدتنا ومصدر اعتزازنا، علمًا لا يُنكّس ولا يُطأطأ.",
        level="success",
        duration_hours=24,
        mark=_glyph("⚑"),
        badge_icon=_glyph("⚑"),
        symbols=(_glyph("⚑"), _glyph("✦")),
        tagline="عهد وميثاق • راية لا تُنكّس",
        deep="#00251c",
        accent="#00794a",
        highlight="#f0e3b2",
        soft="#d6f5e3",
        pattern_css=(
            "repeating-linear-gradient(90deg, transparent 0 30px, "
            "color-mix(in srgb, var(--occasion-highlight) 60%, transparent) 31px 33px)"
        ),
        schedule=OccasionSchedule("gregorian", 3, 11),
    ),
    Occasion(
        key="ramadan",
        name="شهر رمضان المبارك",
        theme_name="ليالي رمضان",
        title="رمضان مبارك",
        body="تقبّل الله صيامكم وقيامكم، وجعله شهر خير وبركة على مدرستنا وطلابها.",
        level="info",
        duration_hours=24,
        mark=_glyph("☾"),
        badge_icon=_glyph("☾"),
        symbols=(_glyph("☾"), _glyph("✦")),
        tagline="شهر الخير • صيام وقيام",
        deep="#101033",
        accent="#4c3f91",
        highlight="#f2d675",
        soft="#e8e4ff",
        pattern_css="radial-gradient(var(--occasion-highlight) 1.5px, transparent 1.6px)",
        mark_shape="46% 54% 58% 42% / 52% 44% 56% 48%",
        schedule=OccasionSchedule("hijri", 9, 1),
    ),
    Occasion(
        key="eid_fitr",
        name="عيد الفطر",
        theme_name="فرحة العيد",
        title="عيدكم مبارك",
        body="كل عام وأنتم بخير. تقبّل الله منا ومنكم صالح الأعمال.",
        level="success",
        duration_hours=24,
        mark=_glyph("✦"),
        badge_icon=_glyph("✦"),
        symbols=(_glyph("✦"), _glyph("☾")),
        tagline="فرحة وتهاني • كل عام وأنتم بخير",
        deep="#0d2b2b",
        accent="#0f8a8a",
        highlight="#ffd97d",
        soft="#ccfbf1",
        pattern_css=(
            "radial-gradient(circle at 50% 50%, var(--occasion-highlight) 2px, transparent 2.5px)"
        ),
        mark_shape="42% 58% 50% 50% / 55% 45% 55% 45%",
        schedule=OccasionSchedule("hijri", 10, 1),
    ),
    Occasion(
        key="eid_adha",
        name="عيد الأضحى",
        theme_name="عيد النحر",
        title="عيد أضحى مبارك",
        body="تقبّل الله طاعتكم، وأعاده عليكم وعلى وطننا بالخير واليمن والبركات.",
        level="success",
        duration_hours=24,
        mark=_glyph("✧"),
        badge_icon=_glyph("✧"),
        symbols=(_glyph("✧"), _glyph("◈")),
        tagline="تقبّل الله • أيام مباركة",
        deep="#2a1a0d",
        accent="#a8641f",
        highlight="#ffd89b",
        soft="#fef3c7",
        pattern_css="repeating-linear-gradient(135deg, transparent 0 24px, var(--occasion-highlight) 25px 27px)",
        mark_shape="52% 48% 44% 56% / 46% 58% 42% 54%",
        schedule=OccasionSchedule("hijri", 12, 10),
    ),
    Occasion(
        key="teachers_day",
        name="يوم المعلم",
        theme_name="المعرفة والعطاء",
        title="شكرًا معلمينا",
        body="بكم تُبنى الأجيال وتزدهر المعرفة. شكرًا لعطائكم المتواصل.",
        level="success",
        duration_hours=24,
        mark=_glyph("✎"),
        badge_icon=_glyph("✎"),
        symbols=(_glyph("✎"), _glyph("❖")),
        tagline="شكر وعرفان • صُنّاع الأجيال",
        deep="#071b31",
        accent="#2563a6",
        highlight="#f2c14e",
        soft="#dbeafe",
        pattern_css=(
            "linear-gradient(var(--occasion-highlight) 1px, transparent 1px), "
            "linear-gradient(90deg, var(--occasion-highlight) 1px, transparent 1px)"
        ),
        mark_shape="2.5rem",
        mark_transform="rotate(2deg)",
        schedule=OccasionSchedule("gregorian", 10, 5),
    ),
    Occasion(
        key="back_to_school",
        name="العودة للدراسة",
        theme_name="بداية مشرقة",
        title="أهلًا بعودتكم",
        body="عام دراسي جديد مليء بالإنجاز والتعلم. نتمنى للجميع بداية موفقة.",
        level="info",
        duration_hours=24,
        mark=_glyph("❖"),
        badge_icon=_glyph("❖"),
        symbols=(_glyph("❖"), _glyph("✦")),
        tagline="بداية مشرقة • طموح جديد",
        deep="#05243c",
        accent="#0e91b7",
        highlight="#ffd166",
        soft="#cffafe",
        pattern_css=(
            "linear-gradient(rgba(255,255,255,.8) 1px, transparent 1px), "
            "linear-gradient(90deg, rgba(255,255,255,.8) 1px, transparent 1px)"
        ),
        mark_shape="38% 62% 56% 44% / 48% 42% 58% 52%",
        # بداية العام الدراسي تتغير سنويًا بقرار الوزارة، فلا تُجدول تلقائيًا.
        schedule=None,
    ),
    Occasion(
        key="graduation",
        name="حفل التخرج",
        theme_name="ليلة الإنجاز",
        title="مبارك التخرج",
        body="نبارك لطلابنا وطالباتنا تخرجهم، ونتمنى لهم مستقبلًا حافلًا بالنجاح.",
        level="success",
        duration_hours=12,
        mark=_glyph("★"),
        badge_icon=_glyph("★"),
        symbols=(_glyph("★"), _glyph("✦")),
        tagline="حصاد الإنجاز • بداية المستقبل",
        deep="#1d1135",
        accent="#7c3bbd",
        highlight="#f4c95d",
        soft="#ede9fe",
        pattern_css="radial-gradient(var(--occasion-highlight) 2px, transparent 2px)",
        mark_shape="50% 50% 42% 58% / 58% 42% 58% 42%",
        # موعد الحفل قرار مدرسي، لا تاريخ ثابت.
        schedule=None,
    ),
)


OCCASIONS: dict[str, Occasion] = {occasion.key: occasion for occasion in _OCCASION_LIST}

# ``weather`` كان مُدرجًا كمناسبة، وهو في الحقيقة تنبيه لا احتفال — ومكرر
# أصلًا في ``EmergencyAlert.KIND_WEATHER`` بمعالجة أقوى (تغطية كاملة للشاشة).
# نُبقي المفتاح معروفًا هنا لأغراض الترحيل فقط.
RETIRED_OCCASION_KEYS = ("weather",)


def get(key: str | None) -> Optional[Occasion]:
    if not key:
        return None
    return OCCASIONS.get(str(key).strip())


def all_occasions() -> tuple[Occasion, ...]:
    return _OCCASION_LIST


def announcement_choices() -> list[tuple[str, str]]:
    """اختيارات حقل ``Announcement.occasion_theme``."""
    return [("", "بدون ثيم مناسبة")] + [(o.key, o.name) for o in _OCCASION_LIST]


def screen_choices() -> list[tuple[str, str]]:
    """اختيارات حقل ``DisplayScreen.occasion_theme``."""
    return [
        ("auto", "تلقائي حسب التنبيه"),
        ("off", "بدون قالب مناسبة"),
    ] + [(o.key, o.name) for o in _OCCASION_LIST]


def theme_map() -> dict[str, dict]:
    """خريطة الثيمات التي تُحقن في الصفحة ليقرأها ``display.js``."""
    return {o.key: o.as_theme_dict() for o in _OCCASION_LIST}


def card_list() -> list[dict]:
    """بطاقات لوحة المدير — بنفس ألوان الشاشة ورموزها، لا معاينة مغايرة."""
    return [o.as_card_dict() for o in _OCCASION_LIST]


def template_map() -> dict[str, dict]:
    """قيم تعبئة نموذج إنشاء التنبيه، مفهرسة بمفتاح المناسبة."""
    return {o.key: o.as_template_dict() for o in _OCCASION_LIST}


@dataclass(frozen=True)
class UpcomingOccasion:
    occasion: Occasion
    occurs_on: date
    days_left: int

    @property
    def is_today(self) -> bool:
        return self.days_left == 0

    @property
    def countdown_label(self) -> str:
        """عبارة العد التنازلي وفق تمييز العدد في العربية.

        القاعدة: ٣–١٠ يُميَّز بجمع القلة (أيام)، و١١ فأكثر بالمفرد المنصوب
        (يومًا). قول «بعد 49 أيام» خطأ نحوي ظاهر لكل قارئ عربي.
        """
        days = self.days_left
        if days == 0:
            return "اليوم"
        if days == 1:
            return "غدًا"
        if days == 2:
            return "بعد يومين"
        if 3 <= days <= 10:
            return f"بعد {days} أيام"
        return f"بعد {days} يومًا"


def upcoming(
    today: date,
    *,
    lead_days: int = SUGGESTION_LEAD_DAYS,
    exclude_keys: Iterable[str] = (),
) -> list[UpcomingOccasion]:
    """المناسبات المجدولة الواقعة خلال ``lead_days`` القادمة.

    تُستخدم للاقتراح في لوحة المدير — لا للتفعيل التلقائي. تفعيل ثيم على
    شاشات مدرسة دون قرار بشري تصرّف لا يُغتفر إن أخطأ، والاقتراح يمنح نفس
    الفائدة بلا هذه المخاطرة.
    """
    excluded = {str(key) for key in exclude_keys}
    results: list[UpcomingOccasion] = []

    for occasion in _OCCASION_LIST:
        if occasion.schedule is None or occasion.key in excluded:
            continue
        occurs_on = occasion.schedule.next_occurrence(today)
        if occurs_on is None:
            continue
        days_left = (occurs_on - today).days
        if 0 <= days_left <= lead_days:
            results.append(UpcomingOccasion(occasion, occurs_on, days_left))

    results.sort(key=lambda item: item.days_left)
    return results


def next_occurrence_for(key: str, today: date) -> Optional[date]:
    occasion = get(key)
    if occasion is None or occasion.schedule is None:
        return None
    return occasion.schedule.next_occurrence(today)


__all__ = [
    "OCCASIONS",
    "RETIRED_OCCASION_KEYS",
    "SUGGESTION_LEAD_DAYS",
    "Occasion",
    "OccasionSchedule",
    "UpcomingOccasion",
    "all_occasions",
    "announcement_choices",
    "card_list",
    "get",
    "next_occurrence_for",
    "screen_choices",
    "template_map",
    "theme_map",
    "upcoming",
]

from decimal import Decimal

from django.db import migrations


PUBLIC_PLAN_CODES = {
    "free-trial",
    "school-basic-semiannual",
    "school-smart-semiannual",
    "school-expanded-semiannual",
    "school-basic-annual",
    "school-smart-annual",
    "school-expanded-annual",
}


def publish_commercial_plan_catalog(apps, schema_editor):
    SubscriptionPlan = apps.get_model("core", "SubscriptionPlan")

    # Legacy plans stay in the database so current subscriptions keep their
    # original limits and commercial terms, but they are no longer offered to
    # new customers or renewals.
    SubscriptionPlan.objects.exclude(code__in=PUBLIC_PLAN_CODES).update(
        is_active=False,
        is_featured=False,
    )

    SubscriptionPlan.objects.update_or_create(
        code="free-trial",
        defaults={
            "name": "تجربة مجانية",
            "description": "جرّب المنصة كاملة وشغّل أول شاشة قبل الاشتراك.",
            "price": Decimal("0.00"),
            "duration_days": 14,
            "max_users": 1,
            "max_screens": 1,
            "max_schools": 1,
            "is_active": True,
            "is_featured": False,
            "show_card_badge": True,
            "show_card_duration": True,
            "show_monthly_equivalent": False,
            "show_screen_limit": True,
            "sort_order": 0,
            "card_badge_text": "14 يومًا مجانًا",
            "card_duration_text": "بدون بطاقة بنكية",
            "card_price_caption": "تجربة كاملة",
            "card_monthly_text": "",
            "card_features": "جميع مزايا المنصة كاملة\nتشغيل شاشة واحدة\nإنشاء الحساب فورًا",
            "card_screen_text": "شاشة واحدة خلال التجربة",
            "card_cta_text": "ابدأ التجربة المجانية",
        },
    )

    common = {
        "max_users": None,
        "max_schools": 1,
        "is_active": True,
        "show_card_badge": True,
        "show_card_duration": True,
        "show_monthly_equivalent": True,
        "show_screen_limit": True,
        "card_cta_text": "اختر هذه الباقة",
    }
    packages = [
        {
            "key": "basic",
            "name": "الأساسية",
            "description": "لبداية عملية على شاشة المدرسة الرئيسية.",
            "screens": 1,
            "order": 10,
            "badge": "للبدء",
            "featured": False,
            "features": (
                "جميع مزايا المنصة كاملة\n"
                "الجداول والانتظار والمناوبات\n"
                "الإعلانات والتكريم وتنبيهات الطوارئ\n"
                "إعداد ذاتي ودعم عبر البريد"
            ),
        },
        {
            "key": "smart",
            "name": "المدرسة الذكية",
            "description": "الخيار الأنسب لتغطية أهم مواقع المدرسة.",
            "screens": 3,
            "order": 20,
            "badge": "الأكثر طلبًا",
            "featured": True,
            "features": (
                "جميع مزايا المنصة كاملة\n"
                "تخصيص مستقل لكل شاشة\n"
                "جلسة تهيئة واستيراد أولي\n"
                "دعم مباشر عبر واتساب"
            ),
        },
        {
            "key": "expanded",
            "name": "التشغيل الموسّع",
            "description": "لتغطية المباني والممرات والمرافق بشاشات متعددة.",
            "screens": 6,
            "order": 30,
            "badge": "للتوسع",
            "featured": False,
            "features": (
                "جميع مزايا المنصة كاملة\n"
                "إطلاق موجه وتوزيع المحتوى\n"
                "دعم فني بأولوية\n"
                "مراجعة تشغيل دورية"
            ),
        },
    ]
    cycles = [
        {
            "key": "semiannual",
            "days": 182,
            "label": "6 أشهر",
            "caption": "ريال سعودي / 6 أشهر",
            "prices": {"basic": "590.00", "smart": "1390.00", "expanded": "2390.00"},
        },
        {
            "key": "annual",
            "days": 365,
            "label": "سنة كاملة",
            "caption": "ريال سعودي / سنوي",
            "prices": {"basic": "990.00", "smart": "2290.00", "expanded": "3990.00"},
        },
    ]

    for cycle in cycles:
        for package in packages:
            defaults = {
                **common,
                "name": package["name"],
                "description": package["description"],
                "price": Decimal(cycle["prices"][package["key"]]),
                "duration_days": cycle["days"],
                "max_screens": package["screens"],
                "is_featured": package["featured"],
                "sort_order": package["order"],
                "card_badge_text": package["badge"],
                "card_duration_text": f"مدة الباقة: {cycle['label']}",
                "card_price_caption": cycle["caption"],
                "card_monthly_text": "",
                "card_features": package["features"],
                "card_screen_text": (
                    f"تشغيل حتى {package['screens']} شاشات"
                    if package["screens"] > 1
                    else "تشغيل شاشة واحدة"
                ),
            }
            SubscriptionPlan.objects.update_or_create(
                code=f"school-{package['key']}-{cycle['key']}",
                defaults=defaults,
            )


class Migration(migrations.Migration):
    dependencies = [("core", "0027_displaypairingsession")]

    operations = [migrations.RunPython(publish_commercial_plan_catalog, migrations.RunPython.noop)]

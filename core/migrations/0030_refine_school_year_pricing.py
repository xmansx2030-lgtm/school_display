from decimal import Decimal

from django.db import migrations


CYCLES = {
    "monthly": {
        "days": 30,
        "label": "شهر واحد",
        "caption": "ريال سعودي / شهري",
        "base_price": Decimal("119.00"),
        "additional_screen_price": Decimal("60.00"),
        "badge": "مرن",
        "duration_text": "تجديد مرن كل 30 يومًا",
        "monthly_text": "أوقف التجديد خلال الإجازة متى احتجت",
    },
    "semiannual": {
        "days": 182,
        "label": "6 أشهر",
        "caption": "ريال سعودي / 6 أشهر",
        "base_price": Decimal("680.00"),
        "additional_screen_price": Decimal("340.00"),
        "badge": "وفر نحو 5%",
        "duration_text": "6 أشهر بسداد واحد",
        "monthly_text": "أوفر من 6 دفعات شهرية",
    },
    "annual": {
        "days": 365,
        "label": "عام دراسي",
        "caption": "ريال سعودي / عام دراسي",
        "base_price": Decimal("1070.00"),
        "additional_screen_price": Decimal("540.00"),
        "badge": "الأوفر للمدارس",
        "duration_text": "عام دراسي — صلاحية 12 شهرًا",
        "monthly_text": "ادفع ما يقارب 9 أشهر واحصل على صلاحية 12 شهرًا",
    },
}

SCREEN_NAMES = {
    1: "شاشة واحدة",
    2: "شاشتين",
    3: "3 شاشات",
    4: "4 شاشات",
    5: "5 شاشات",
}

ACTIVE_CODES = {
    "free-trial",
    *{
        f"school-screen-{screen_count}-{cycle_key}"
        for cycle_key in CYCLES
        for screen_count in SCREEN_NAMES
    },
}


def refine_school_year_pricing(apps, schema_editor):
    SubscriptionPlan = apps.get_model("core", "SubscriptionPlan")

    # Historical plans remain available to linked subscriptions, but only the
    # current catalog is offered for new purchases and renewals.
    SubscriptionPlan.objects.exclude(code__in=ACTIVE_CODES).update(
        is_active=False,
        is_featured=False,
    )

    common_features = (
        "الجداول والانتظار والمناوبات في مكان واحد\n"
        "الإعلانات والتكريم وتنبيهات الطوارئ\n"
        "تخصيص مستقل لمحتوى كل شاشة\n"
        "استيراد Excel وتحديث المحتوى فورًا\n"
        "استمرار العرض عند ضعف الاتصال\n"
        "مراقبة حالة الشاشات ودعم عربي"
    )

    for cycle_key, cycle in CYCLES.items():
        for screen_count, screen_name in SCREEN_NAMES.items():
            price = cycle["base_price"] + (
                cycle["additional_screen_price"] * (screen_count - 1)
            )
            SubscriptionPlan.objects.update_or_create(
                code=f"school-screen-{screen_count}-{cycle_key}",
                defaults={
                    "name": screen_name,
                    "description": (
                        "كل أدوات تشغيل شاشات المدرسة، بسعر واضح ودون حجب أي ميزة."
                    ),
                    "price": price,
                    "duration_days": cycle["days"],
                    "max_users": None,
                    "max_screens": screen_count,
                    "max_schools": 1,
                    "is_active": True,
                    "is_featured": False,
                    "show_card_badge": True,
                    "show_card_duration": True,
                    "show_monthly_equivalent": True,
                    "show_screen_limit": True,
                    "sort_order": screen_count * 10,
                    "card_badge_text": cycle["badge"],
                    "card_duration_text": cycle["duration_text"],
                    "card_price_caption": cycle["caption"],
                    "card_monthly_text": cycle["monthly_text"],
                    "card_features": common_features,
                    "card_screen_text": (
                        "تشغيل شاشة واحدة"
                        if screen_count == 1
                        else "تشغيل شاشتين"
                        if screen_count == 2
                        else f"تشغيل حتى {screen_count} شاشات"
                    ),
                    "card_cta_text": "ابدأ التجربة واختر الباقة",
                },
            )


class Migration(migrations.Migration):
    dependencies = [("core", "0029_publish_screen_count_pricing")]

    operations = [
        migrations.RunPython(refine_school_year_pricing, migrations.RunPython.noop)
    ]

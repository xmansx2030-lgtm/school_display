from decimal import Decimal

from django.db import migrations


CYCLES = {
    "monthly": {
        "days": 30,
        "label": "شهر واحد",
        "caption": "ريال سعودي / شهري",
        "base_price": Decimal("99.00"),
        "additional_screen_price": Decimal("60.00"),
    },
    "semiannual": {
        "days": 182,
        "label": "6 أشهر",
        "caption": "ريال سعودي / 6 أشهر",
        "base_price": Decimal("590.00"),
        "additional_screen_price": Decimal("360.00"),
    },
    "annual": {
        "days": 365,
        "label": "سنة كاملة",
        "caption": "ريال سعودي / سنوي",
        "base_price": Decimal("990.00"),
        "additional_screen_price": Decimal("600.00"),
    },
}

SCREEN_NAMES = {
    1: "شاشة واحدة",
    2: "شاشتين",
    3: "3 شاشات",
    4: "4 شاشات",
    5: "5 شاشات",
}

PUBLIC_PLAN_CODES = {
    "free-trial",
    *{
        f"school-screen-{screen_count}-{cycle_key}"
        for cycle_key in CYCLES
        for screen_count in SCREEN_NAMES
    },
}


def publish_screen_count_pricing(apps, schema_editor):
    SubscriptionPlan = apps.get_model("core", "SubscriptionPlan")

    # Keep previous plans and linked subscriptions intact, while removing old
    # catalog options from new orders and renewals.
    SubscriptionPlan.objects.exclude(code__in=PUBLIC_PLAN_CODES).update(
        is_active=False,
        is_featured=False,
    )

    common = {
        "description": "جميع مزايا المنصة مشمولة؛ اختر فقط عدد الشاشات ومدة الاشتراك.",
        "max_users": None,
        "max_schools": 1,
        "is_active": True,
        "is_featured": False,
        "show_card_badge": True,
        "show_card_duration": True,
        "show_monthly_equivalent": True,
        "show_screen_limit": True,
        "card_features": (
            "جميع مزايا المنصة كاملة\n"
            "الجداول والانتظار والمناوبات\n"
            "الإعلانات والتكريم وتنبيهات الطوارئ\n"
            "تخصيص مستقل لمحتوى كل شاشة\n"
            "الدعم الفني والتحديثات المستمرة"
        ),
        "card_cta_text": "اختر هذه الباقة",
    }

    for cycle_key, cycle in CYCLES.items():
        for screen_count, screen_name in SCREEN_NAMES.items():
            price = cycle["base_price"] + (
                cycle["additional_screen_price"] * (screen_count - 1)
            )
            SubscriptionPlan.objects.update_or_create(
                code=f"school-screen-{screen_count}-{cycle_key}",
                defaults={
                    **common,
                    "name": screen_name,
                    "price": price,
                    "duration_days": cycle["days"],
                    "max_screens": screen_count,
                    "sort_order": screen_count * 10,
                    "card_badge_text": screen_name,
                    "card_duration_text": f"مدة الباقة: {cycle['label']}",
                    "card_price_caption": cycle["caption"],
                    "card_monthly_text": "",
                    "card_screen_text": (
                        "تشغيل شاشة واحدة"
                        if screen_count == 1
                        else "تشغيل شاشتين"
                        if screen_count == 2
                        else f"تشغيل حتى {screen_count} شاشات"
                    ),
                },
            )


class Migration(migrations.Migration):
    dependencies = [("core", "0028_publish_commercial_plan_catalog")]

    operations = [
        migrations.RunPython(publish_screen_count_pricing, migrations.RunPython.noop)
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_displayscreen_customization"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="card_badge_text",
            field=models.CharField(
                blank=True,
                help_text="اتركه فارغًا لاستخدام النص التلقائي بحسب نوع الباقة.",
                max_length=80,
                verbose_name="نص شارة الباقة",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="card_cta_text",
            field=models.CharField(
                blank=True,
                default="اطلب هذه الباقة",
                max_length=80,
                verbose_name="نص زر الطلب",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="card_duration_text",
            field=models.CharField(
                blank=True,
                help_text="مثال: مدة الباقة: 6 أشهر. اتركه فارغًا ليُنشأ من عدد الأيام.",
                max_length=120,
                verbose_name="نص مدة الباقة",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="card_features",
            field=models.TextField(
                blank=True,
                default="جميع مزايا النظام كاملة",
                help_text="اكتب كل ميزة في سطر مستقل. يمكن ترك الحقل فارغًا لإخفاء المزايا.",
                verbose_name="مزايا الباقة",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="card_monthly_text",
            field=models.CharField(
                blank=True,
                help_text="اتركه فارغًا ليُحسب المعادل الشهري تلقائيًا من السعر والمدة.",
                max_length=160,
                verbose_name="نص المعادل الشهري",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="card_price_caption",
            field=models.CharField(
                blank=True,
                help_text="مثال: ريال سعودي / نصف سنوي. اتركه فارغًا للنص التلقائي.",
                max_length=120,
                verbose_name="وصف السعر",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="card_screen_text",
            field=models.CharField(
                blank=True,
                help_text="مثال: الشاشات: 3. اتركه فارغًا ليُنشأ من حد الشاشات.",
                max_length=120,
                verbose_name="نص عدد الشاشات",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="show_card_badge",
            field=models.BooleanField(default=True, verbose_name="إظهار شارة الباقة"),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="show_card_duration",
            field=models.BooleanField(default=True, verbose_name="إظهار مدة الباقة"),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="show_monthly_equivalent",
            field=models.BooleanField(default=True, verbose_name="إظهار المعادل الشهري"),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="show_screen_limit",
            field=models.BooleanField(default=True, verbose_name="إظهار عدد الشاشات"),
        ),
    ]

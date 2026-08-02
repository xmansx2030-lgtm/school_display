from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_subscriptionplan_catalog_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="displayscreen",
            name="featured_panel_override",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "استخدام اختيار المدرسة"),
                    ("excellence", "لوحة الشرف"),
                    ("duty", "الإشراف والمناوبة"),
                ],
                default="",
                help_text="يُستخدم عندما تكون لوحة الشرف والإشراف والمناوبة مفعّلتين معًا.",
                max_length=20,
                verbose_name="الكرت المميز",
            ),
        ),
        migrations.AddField(
            model_name="displayscreen",
            name="occasion_theme",
            field=models.CharField(
                choices=[
                    ("auto", "تلقائي حسب التنبيه"),
                    ("off", "بدون قالب مناسبة"),
                    ("national_day", "اليوم الوطني السعودي"),
                    ("founding_day", "يوم التأسيس"),
                    ("teachers_day", "يوم المعلم"),
                    ("back_to_school", "العودة للدراسة"),
                    ("graduation", "حفل التخرج"),
                    ("weather", "حالة جوية"),
                ],
                default="auto",
                help_text="يمكن تفعيله تلقائيًا من التنبيهات أو تثبيت قالب لهذه الشاشة فقط.",
                max_length=30,
                verbose_name="قالب المناسبة",
            ),
        ),
        migrations.AddField(
            model_name="displayscreen",
            name="show_announcements",
            field=models.BooleanField(default=True, verbose_name="إظهار التنبيهات المدرسية"),
        ),
        migrations.AddField(
            model_name="displayscreen",
            name="show_duty",
            field=models.BooleanField(default=True, verbose_name="إظهار الإشراف والمناوبة"),
        ),
        migrations.AddField(
            model_name="displayscreen",
            name="show_excellence",
            field=models.BooleanField(default=True, verbose_name="إظهار لوحة الشرف"),
        ),
        migrations.AddField(
            model_name="displayscreen",
            name="show_period_classes",
            field=models.BooleanField(default=True, verbose_name="إظهار جدول الحصص الجارية"),
        ),
        migrations.AddField(
            model_name="displayscreen",
            name="show_standby",
            field=models.BooleanField(default=True, verbose_name="إظهار حصص الانتظار"),
        ),
        migrations.AddField(
            model_name="displayscreen",
            name="theme_override",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "استخدام ثيم المدرسة"),
                    ("indigo", "أزرق/نيلي"),
                    ("emerald", "أخضر"),
                    ("rose", "وردي"),
                    ("cyan", "سماوي"),
                    ("amber", "أصفر"),
                    ("orange", "برتقالي"),
                    ("violet", "بنفسجي"),
                ],
                default="",
                help_text="اتركه على ثيم المدرسة لتتبع الإعداد العام تلقائيًا.",
                max_length=20,
                verbose_name="الثيم الخاص بالشاشة",
            ),
        ),
    ]

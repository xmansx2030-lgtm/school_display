from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_alter_displayscreen_occasion_theme"),
    ]

    operations = [
        migrations.AddField(
            model_name="displayscreen",
            name="monitor_always_on",
            field=models.BooleanField(
                default=False,
                help_text="فعّله للشاشات التي يُفترض بقاؤها تعمل خارج أوقات الدوام أيضًا.",
                verbose_name="مراقبة على مدار الساعة",
            ),
        ),
        migrations.AddField(
            model_name="screenoutage",
            name="cause",
            field=models.CharField(
                choices=[
                    ("platform", "عطل في المنصة"),
                    ("school_network", "انقطاع إنترنت أو كهرباء المدرسة"),
                    ("device_off", "جهاز العرض مطفأ أو أُغلقت الصفحة"),
                    ("network_drop", "انقطاع مفاجئ في الشبكة"),
                    ("ws_timeout", "توقف الاتصال اللحظي"),
                    ("binding_lost", "الشاشة مرتبطة بجهاز آخر"),
                    ("never_connected", "لم يتم تشغيل الشاشة بعد"),
                    ("unknown", "سبب غير محدد"),
                ],
                default="unknown",
                max_length=32,
                verbose_name="السبب",
            ),
        ),
        migrations.AddField(
            model_name="screenoutage",
            name="cause_detail",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="تفاصيل السبب"),
        ),
        migrations.AddField(
            model_name="screenoutage",
            name="cause_confidence",
            field=models.CharField(
                choices=[("confirmed", "مؤكد"), ("likely", "مرجّح")],
                default="likely",
                max_length=12,
                verbose_name="درجة الثقة",
            ),
        ),
        migrations.AddField(
            model_name="screenoutage",
            name="scope",
            field=models.CharField(
                choices=[
                    ("screen", "شاشة واحدة"),
                    ("school", "المدرسة كاملة"),
                    ("platform", "المنصة"),
                ],
                default="screen",
                max_length=16,
                verbose_name="نطاق العطل",
            ),
        ),
        migrations.AddField(
            model_name="screenoutage",
            name="close_code",
            field=models.IntegerField(blank=True, null=True, verbose_name="رمز إغلاق الاتصال"),
        ),
        migrations.AddField(
            model_name="screenoutage",
            name="suppressed_reason",
            field=models.CharField(
                blank=True,
                default="",
                help_text="يُسجَّل العطل دائمًا للتقارير حتى لو لم يُرسل عنه تنبيه.",
                max_length=32,
                verbose_name="سبب عدم الإرسال",
            ),
        ),
        migrations.AddField(
            model_name="screenoutage",
            name="recovery_notified_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="وقت إشعار عودة الاتصال"),
        ),
        migrations.AddField(
            model_name="screenoutage",
            name="alert_count",
            field=models.PositiveSmallIntegerField(default=0, verbose_name="عدد التنبيهات المرسلة"),
        ),
        migrations.AddIndex(
            model_name="screenoutage",
            index=models.Index(fields=["screen", "alert_sent_at"], name="screen_outage_alert_idx"),
        ),
    ]

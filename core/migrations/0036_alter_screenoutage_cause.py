from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_screen_outage_diagnostics"),
    ]

    operations = [
        migrations.AlterField(
            model_name="screenoutage",
            name="cause",
            field=models.CharField(
                choices=[
                    ("platform", "عطل في المنصة"),
                    ("school_closed", "المدرسة مغلقة (إجازة)"),
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
    ]

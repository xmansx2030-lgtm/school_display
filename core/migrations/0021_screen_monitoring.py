from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("core", "0020_userprofile_needs_onboarding")]

    operations = [
        migrations.CreateModel(
            name="ScreenOutage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("detected_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="وقت اكتشاف التعطل")),
                ("last_seen_at", models.DateTimeField(blank=True, null=True, verbose_name="آخر ظهور قبل التعطل")),
                ("alert_sent_at", models.DateTimeField(blank=True, null=True, verbose_name="وقت إرسال التنبيه")),
                ("resolved_at", models.DateTimeField(blank=True, null=True, verbose_name="وقت عودة الاتصال")),
                ("screen", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="outages", to="core.displayscreen", verbose_name="الشاشة")),
            ],
            options={"verbose_name": "تعطل شاشة", "verbose_name_plural": "أعطال الشاشات", "ordering": ("-detected_at",)},
        ),
        migrations.CreateModel(
            name="ScreenWeeklyUptimeReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("week_start", models.DateField(verbose_name="بداية الأسبوع")),
                ("week_end", models.DateField(verbose_name="نهاية الأسبوع")),
                ("uptime_percent", models.DecimalField(decimal_places=2, default=100, max_digits=5, verbose_name="نسبة التشغيل")),
                ("offline_seconds", models.PositiveBigIntegerField(default=0, verbose_name="ثواني الانقطاع")),
                ("sent_at", models.DateTimeField(blank=True, null=True, verbose_name="وقت إرسال التقرير")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("screen", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="weekly_uptime_reports", to="core.displayscreen", verbose_name="الشاشة")),
            ],
            options={"verbose_name": "تقرير تشغيل أسبوعي", "verbose_name_plural": "تقارير التشغيل الأسبوعية", "ordering": ("-week_start", "screen__name")},
        ),
        migrations.AddIndex(model_name="screenoutage", index=models.Index(fields=["screen", "resolved_at"], name="screen_outage_open_idx")),
        migrations.AddIndex(model_name="screenoutage", index=models.Index(fields=["detected_at"], name="screen_outage_detect_idx")),
        migrations.AddConstraint(model_name="screenweeklyuptimereport", constraint=models.UniqueConstraint(fields=("screen", "week_start"), name="uq_screen_uptime_week")),
    ]

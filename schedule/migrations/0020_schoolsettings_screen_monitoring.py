from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("schedule", "0019_schoolsettings_display_after_holiday_badge_and_more")]

    operations = [
        migrations.AddField(
            model_name="schoolsettings",
            name="screen_offline_alerts_enabled",
            field=models.BooleanField(default=True, verbose_name="تنبيه عند تعطل شاشة"),
        ),
        migrations.AddField(
            model_name="schoolsettings",
            name="screen_offline_threshold_minutes",
            field=models.PositiveSmallIntegerField(
                default=10,
                help_text="يرسل النظام تنبيهًا إذا لم تتصل الشاشة خلال هذه المدة.",
                validators=[MinValueValidator(5), MaxValueValidator(60)],
                verbose_name="مدة الانتظار قبل التنبيه (دقائق)",
            ),
        ),
        migrations.AddField(
            model_name="schoolsettings",
            name="screen_offline_email_enabled",
            field=models.BooleanField(default=True, verbose_name="إرسال تنبيه التعطل بالبريد"),
        ),
        migrations.AddField(
            model_name="schoolsettings",
            name="weekly_uptime_report_enabled",
            field=models.BooleanField(default=True, verbose_name="إرسال تقرير التشغيل الأسبوعي"),
        ),
    ]

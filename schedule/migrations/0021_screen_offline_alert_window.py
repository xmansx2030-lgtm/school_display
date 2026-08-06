import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("schedule", "0020_schoolsettings_screen_monitoring"),
    ]

    operations = [
        migrations.AddField(
            model_name="schoolsettings",
            name="screen_offline_school_hours_only",
            field=models.BooleanField(
                default=True,
                help_text="لا يُرسل تنبيه بعد انتهاء الدوام أو في الإجازات، ويبقى الانقطاع مسجّلًا في التقرير الأسبوعي.",
                verbose_name="التنبيه أثناء الدوام فقط",
            ),
        ),
        migrations.AddField(
            model_name="schoolsettings",
            name="screen_offline_grace_minutes",
            field=models.PositiveSmallIntegerField(
                default=15,
                help_text="فترة سماح بعد بداية اليوم الدراسي قبل إرسال أول تنبيه.",
                validators=[django.core.validators.MaxValueValidator(120)],
                verbose_name="مهلة بداية الدوام (دقائق)",
            ),
        ),
        migrations.AddField(
            model_name="schoolsettings",
            name="screen_offline_cooldown_minutes",
            field=models.PositiveSmallIntegerField(
                default=120,
                help_text="يمنع تكرار التنبيه عن الشاشة نفسها خلال هذه المدة.",
                validators=[
                    django.core.validators.MinValueValidator(10),
                    django.core.validators.MaxValueValidator(720),
                ],
                verbose_name="أقل فاصل بين تنبيهين (دقائق)",
            ),
        ),
        migrations.AddField(
            model_name="schoolsettings",
            name="screen_offline_max_alerts_per_day",
            field=models.PositiveSmallIntegerField(
                default=3,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(20),
                ],
                verbose_name="أقصى عدد تنبيهات لكل شاشة يوميًا",
            ),
        ),
        migrations.AddField(
            model_name="schoolsettings",
            name="screen_recovery_notice_enabled",
            field=models.BooleanField(
                default=True,
                help_text="يرسل رسالة قصيرة عند عودة الشاشة مع مدة الانقطاع.",
                verbose_name="إشعار عند عودة الاتصال",
            ),
        ),
    ]

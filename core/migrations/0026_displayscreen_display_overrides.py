from django.db import migrations, models

import core.models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_systememployeeprofile"),
    ]

    operations = [
        migrations.AddField(
            model_name="displayscreen",
            name="logo_override",
            field=models.ImageField(
                blank=True,
                help_text="اتركه فارغًا لاستخدام شعار المدرسة العام.",
                null=True,
                upload_to="screens/logos/",
                validators=[core.models._validate_image_extension],
                verbose_name="شعار هذه الشاشة",
            ),
        ),
        migrations.AddField(
            model_name="displayscreen",
            name="display_accent_color_override",
            field=models.CharField(blank=True, default="", help_text="اتركه فارغًا لاستخدام لون جميع الشاشات.", max_length=7, verbose_name="لون العرض الخاص بالشاشة"),
        ),
        migrations.AddField(model_name="displayscreen", name="standby_scroll_speed_override", field=models.FloatField(blank=True, null=True, verbose_name="سرعة تمرير الانتظار لهذه الشاشة")),
        migrations.AddField(model_name="displayscreen", name="periods_scroll_speed_override", field=models.FloatField(blank=True, null=True, verbose_name="سرعة تمرير جدول الحصص لهذه الشاشة")),
        migrations.AddField(model_name="displayscreen", name="display_before_title_override", field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="displayscreen", name="display_before_badge_override", field=models.CharField(blank=True, default="", max_length=40)),
        migrations.AddField(model_name="displayscreen", name="display_after_title_override", field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="displayscreen", name="display_after_badge_override", field=models.CharField(blank=True, default="", max_length=40)),
        migrations.AddField(model_name="displayscreen", name="display_after_holiday_title_override", field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="displayscreen", name="display_after_holiday_badge_override", field=models.CharField(blank=True, default="", max_length=40)),
        migrations.AddField(model_name="displayscreen", name="display_holiday_title_override", field=models.CharField(blank=True, default="", max_length=150)),
        migrations.AddField(model_name="displayscreen", name="display_holiday_badge_override", field=models.CharField(blank=True, default="", max_length=40)),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_displayscreen_customization"),
        ("notices", "0009_announcement_occasion_theme"),
    ]

    operations = [
        migrations.AddField(
            model_name="announcement",
            name="screens",
            field=models.ManyToManyField(
                blank=True,
                help_text="اتركها فارغة لعرض التنبيه على جميع شاشات المدرسة.",
                related_name="announcements",
                to="core.displayscreen",
                verbose_name="شاشات محددة",
            ),
        ),
    ]

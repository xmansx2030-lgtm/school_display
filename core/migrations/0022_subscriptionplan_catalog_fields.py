from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_screen_monitoring"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionplan",
            name="description",
            field=models.CharField(
                blank=True,
                help_text="يظهر أسفل اسم الباقة في صفحة الاشتراك وصفحة الهبوط.",
                max_length=240,
                verbose_name="وصف مختصر",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="is_featured",
            field=models.BooleanField(
                default=False,
                help_text="تميّز الباقة بصرياً وتعرض عليها شارة الأكثر طلباً.",
                verbose_name="الباقة الموصى بها",
            ),
        ),
    ]

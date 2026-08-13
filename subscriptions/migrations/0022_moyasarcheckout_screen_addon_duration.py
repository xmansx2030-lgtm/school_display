from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0021_remove_tamara"),
    ]

    operations = [
        migrations.AddField(
            model_name="moyasarcheckout",
            name="screen_addon_validity_days",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="مدة شراء الشاشات الإضافية كما تم احتسابها عند الدفع.",
                null=True,
                verbose_name="مدة الشاشات الإضافية بالأيام",
            ),
        ),
        migrations.AddField(
            model_name="moyasarcheckout",
            name="screen_addon_ends_at",
            field=models.DateField(
                blank=True,
                help_text="تُستخدم لطلبات شراء الشاشات فقط حتى تطابق التفعيل قيمة الدفع.",
                null=True,
                verbose_name="نهاية صلاحية الشاشات الإضافية",
            ),
        ),
    ]

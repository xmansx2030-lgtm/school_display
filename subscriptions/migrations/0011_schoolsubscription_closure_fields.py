from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0010_alter_subscriptionrequest_receipt_image")]

    operations = [
        migrations.AddField(
            model_name="schoolsubscription",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="وقت تسجيل الإغلاق"),
        ),
        migrations.AddField(
            model_name="schoolsubscription",
            name="closure_notes",
            field=models.TextField(blank=True, verbose_name="تفاصيل سبب الإلغاء أو عدم التجديد"),
        ),
        migrations.AddField(
            model_name="schoolsubscription",
            name="closure_reason",
            field=models.CharField(blank=True, choices=[("budget", "الميزانية"), ("not_used", "ضعف الاستخدام"), ("technical", "مشكلة تقنية"), ("competitor", "الانتقال إلى منافس"), ("school_closed", "إغلاق أو دمج المدرسة"), ("no_response", "تعذر التواصل"), ("other", "سبب آخر")], max_length=30, verbose_name="سبب الإلغاء أو عدم التجديد"),
        ),
    ]

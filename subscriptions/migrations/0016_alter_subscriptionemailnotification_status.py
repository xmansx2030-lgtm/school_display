from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0015_alter_subscriptioninvoice_payment_method_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="subscriptionemailnotification",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "بانتظار الإرسال"),
                    ("processing", "جارٍ الإرسال"),
                    ("sent", "تم الإرسال"),
                    ("skipped", "تم التجاوز"),
                    ("failed", "فشل نهائي"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]

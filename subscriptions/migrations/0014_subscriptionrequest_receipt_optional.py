import subscriptions.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("subscriptions", "0013_merge_subscription_email_tamara"),
    ]

    operations = [
        migrations.AlterField(
            model_name="subscriptionrequest",
            name="receipt_image",
            field=models.ImageField(
                blank=True,
                max_length=500,
                upload_to="receipts/subscription_requests/%Y/%m",
                validators=[subscriptions.models._validate_receipt_extension],
                verbose_name="إيصال التحويل (صورة)",
            ),
        ),
    ]

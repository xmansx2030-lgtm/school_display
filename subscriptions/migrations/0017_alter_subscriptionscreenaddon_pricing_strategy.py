from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0016_alter_subscriptionemailnotification_status")]

    operations = [
        migrations.AlterField(
            model_name="subscriptionscreenaddon",
            name="pricing_strategy",
            field=models.CharField(
                choices=[
                    ("auto_bundle", "تلقائي (شرائح)"),
                    ("manual_bundle", "يدوي (مبلغ إجمالي)"),
                    ("manual_per_screen", "يدوي (سعر لكل شاشة)"),
                ],
                default="auto_bundle",
                help_text=(
                    "التلقائي: 60 ر.س شهريًا لكل شاشة إضافية. "
                    "نصف السنوي ×6، والسنوي ×10 (شهران مجانًا)."
                ),
                max_length=30,
                verbose_name="طريقة التسعير",
            ),
        ),
    ]

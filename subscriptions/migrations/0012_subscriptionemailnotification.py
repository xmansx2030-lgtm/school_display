import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0011_schoolsubscription_closure_fields")]

    operations = [
        migrations.CreateModel(
            name="SubscriptionEmailNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("invoice", "فاتورة اشتراك"), ("expiry", "تنبيه قرب انتهاء الاشتراك")], max_length=20)),
                ("recipient", models.EmailField(max_length=254)),
                ("reminder_days", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("dedupe_key", models.CharField(max_length=255, unique=True)),
                ("status", models.CharField(choices=[("pending", "بانتظار الإرسال"), ("processing", "جارٍ الإرسال"), ("sent", "تم الإرسال"), ("failed", "فشل نهائي")], default="pending", max_length=20)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("available_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="email_notifications", to="subscriptions.subscriptioninvoice")),
                ("subscription", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="email_notifications", to="subscriptions.schoolsubscription")),
            ],
            options={
                "verbose_name": "إشعار بريد اشتراك",
                "verbose_name_plural": "إشعارات بريد الاشتراكات",
                "ordering": ("created_at", "id"),
            },
        ),
        migrations.AddIndex(
            model_name="subscriptionemailnotification",
            index=models.Index(fields=["status", "available_at"], name="sub_email_status_available_idx"),
        ),
    ]

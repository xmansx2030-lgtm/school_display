import django.db.models.deletion
import subscriptions.models
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("subscriptions", "0011_schoolsubscription_closure_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="TamaraCheckout",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("merchant_reference", models.CharField(default=subscriptions.models._tamara_reference, editable=False, max_length=40, unique=True, verbose_name="مرجع التاجر")),
                ("tamara_order_id", models.CharField(blank=True, max_length=80, null=True, unique=True, verbose_name="رقم طلب تمارا")),
                ("checkout_id", models.CharField(blank=True, default="", max_length=80, verbose_name="رقم جلسة الدفع")),
                ("checkout_url", models.URLField(blank=True, default="", max_length=2048, verbose_name="رابط الدفع")),
                ("request_type", models.CharField(choices=[("renewal", "طلب تجديد"), ("new", "طلب اشتراك جديد")], max_length=20)),
                ("starts_at", models.DateField(default=django.utils.timezone.localdate, verbose_name="بداية الاشتراك المطلوبة")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10, verbose_name="المبلغ")),
                ("currency", models.CharField(default="SAR", max_length=3, verbose_name="العملة")),
                ("status", models.CharField(choices=[("initiated", "بدأت"), ("new", "بانتظار الدفع"), ("approved", "تمت الموافقة"), ("authorised", "مصرح بها"), ("captured", "مكتملة"), ("declined", "مرفوضة"), ("canceled", "ملغاة"), ("expired", "منتهية"), ("refunded", "مستردة"), ("error", "خطأ")], default="initiated", max_length=20)),
                ("last_event", models.CharField(blank=True, default="", max_length=40, verbose_name="آخر حدث")),
                ("error_message", models.CharField(blank=True, default="", max_length=300, verbose_name="رسالة الخطأ")),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tamara_checkouts", to=settings.AUTH_USER_MODEL, verbose_name="أنشئت بواسطة")),
                ("payment_operation", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="tamara_checkout", to="subscriptions.subscriptionpaymentoperation")),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tamara_checkouts", to="core.subscriptionplan", verbose_name="الخطة")),
                ("school", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tamara_checkouts", to="core.school", verbose_name="المدرسة")),
                ("subscription", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="tamara_checkouts", to="subscriptions.schoolsubscription")),
            ],
            options={
                "verbose_name": "جلسة دفع تمارا",
                "verbose_name_plural": "جلسات دفع تمارا",
                "ordering": ("-created_at", "-id"),
            },
        ),
        migrations.AddIndex(
            model_name="tamaracheckout",
            index=models.Index(fields=["school", "status", "created_at"], name="tamara_school_status_idx"),
        ),
    ]

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_screen_monitoring"),
        ("notices", "0007_alter_excellence_photo"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EmergencyAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("evacuation", "إخلاء"), ("fire", "حريق"), ("weather", "حالة جوية"), ("suspension", "تعليق دراسة"), ("urgent", "رسالة عاجلة")], max_length=20, verbose_name="نوع التنبيه")),
                ("title", models.CharField(max_length=120, verbose_name="العنوان")),
                ("message", models.TextField(max_length=600, verbose_name="الرسالة")),
                ("is_active", models.BooleanField(default=True, verbose_name="نشط")),
                ("starts_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="وقت البدء")),
                ("expires_at", models.DateTimeField(blank=True, null=True, verbose_name="وقت الانتهاء")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True, verbose_name="وقت الإلغاء")),
                ("cancelled_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="emergency_alerts_cancelled", to=settings.AUTH_USER_MODEL, verbose_name="ألغاه")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="emergency_alerts_created", to=settings.AUTH_USER_MODEL, verbose_name="أرسله")),
                ("schools", models.ManyToManyField(related_name="emergency_alerts", to="core.school", verbose_name="المدارس")),
                ("screens", models.ManyToManyField(blank=True, help_text="اتركها فارغة لإرسال التنبيه إلى جميع شاشات المدارس المحددة.", related_name="emergency_alerts", to="core.displayscreen", verbose_name="شاشات محددة")),
            ],
            options={"verbose_name": "تنبيه طارئ", "verbose_name_plural": "التنبيهات الطارئة", "ordering": ("-created_at",)},
        ),
        migrations.AddIndex(model_name="emergencyalert", index=models.Index(fields=["is_active", "starts_at"], name="emergency_active_idx")),
    ]

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_alter_screenoutage_cause"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserSessionState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "active_session_key",
                    models.CharField(
                        blank=True,
                        default="",
                        max_length=40,
                        verbose_name="مفتاح الجلسة النشطة",
                    ),
                ),
                ("activated_at", models.DateTimeField(auto_now=True, verbose_name="وقت تفعيل الجلسة")),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="session_state",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="المستخدم",
                    ),
                ),
            ],
            options={
                "verbose_name": "جلسة مستخدم نشطة",
                "verbose_name_plural": "جلسات المستخدمين النشطة",
            },
        ),
    ]

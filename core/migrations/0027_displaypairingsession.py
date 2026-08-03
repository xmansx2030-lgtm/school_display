import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_displayscreen_display_overrides"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DisplayPairingSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("user_code", models.CharField(db_index=True, max_length=6, verbose_name="رمز الربط")),
                ("device_id", models.CharField(max_length=64, verbose_name="معرّف جهاز التلفاز")),
                ("device_secret_hash", models.CharField(max_length=64, verbose_name="بصمة سر الجهاز")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "بانتظار الموافقة"),
                            ("approved", "تم الربط"),
                            ("expired", "منتهي"),
                            ("cancelled", "ملغي"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=12,
                        verbose_name="الحالة",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approved_display_pairings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "screen",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pairing_sessions",
                        to="core.displayscreen",
                    ),
                ),
            ],
            options={
                "verbose_name": "جلسة ربط تلفاز",
                "verbose_name_plural": "جلسات ربط التلفاز",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="displaypairingsession",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("user_code",),
                name="unique_pending_display_pairing_code",
            ),
        ),
    ]

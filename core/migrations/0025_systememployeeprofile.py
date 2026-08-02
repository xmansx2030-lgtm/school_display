from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


LEGACY_SUPPORT_PERMISSIONS = [
    "dashboard.view",
    "schools.view",
    "users.view",
    "subscriptions.view",
    "support.view",
    "support.manage",
]


def migrate_legacy_support_users(apps, schema_editor):
    UserModel = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    EmployeeProfile = apps.get_model("core", "SystemEmployeeProfile")
    legacy_users = UserModel.objects.filter(groups__name="Support", is_superuser=False).distinct()
    for user in legacy_users.iterator():
        EmployeeProfile.objects.get_or_create(
            user=user,
            defaults={
                "role": "support",
                "permission_keys": LEGACY_SUPPORT_PERMISSIONS,
            },
        )
        if not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_subscriptionplan_card_content"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SystemEmployeeProfile",
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
                    "role",
                    models.CharField(
                        choices=[
                            ("support", "الدعم الفني"),
                            ("finance", "الإدارة المالية"),
                            ("customer_success", "نجاح العملاء"),
                            ("operations", "إدارة العمليات"),
                            ("custom", "دور مخصص"),
                        ],
                        default="support",
                        max_length=32,
                        verbose_name="الدور الوظيفي",
                    ),
                ),
                (
                    "permission_keys",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text="قائمة مفاتيح الصلاحيات الممنوحة لهذا الموظف.",
                        verbose_name="صلاحيات المنصة",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_system_employees",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="أنشأه",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="system_employee_profile",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="الموظف",
                    ),
                ),
            ],
            options={
                "verbose_name": "موظف منصة",
                "verbose_name_plural": "موظفو المنصة",
                "ordering": ("user__first_name", "user__username"),
            },
        ),
        migrations.RunPython(migrate_legacy_support_users, migrations.RunPython.noop),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_usertwofactorauth"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="needs_onboarding",
            field=models.BooleanField(
                default=False,
                help_text="يوجّه المستخدم الجديد إلى دليل البدء مرة واحدة بعد أول دخول.",
                verbose_name="يحتاج إلى دليل البدء",
            ),
        ),
    ]

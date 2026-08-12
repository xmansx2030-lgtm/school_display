from django.db import migrations


class Migration(migrations.Migration):
    """Screen outage alerts go to the platform administrator over Telegram only.

    Schools are no longer mailed about screen faults, so the per-school opt-out
    for those messages has nothing left to switch.
    """

    dependencies = [
        ("schedule", "0022_schoolclosure"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="schoolsettings",
            name="screen_offline_email_enabled",
        ),
    ]

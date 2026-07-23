from django.apps import AppConfig


class TelegramAlertsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "telegram_alerts"
    verbose_name = "تنبيهات تيليجرام"

    def ready(self) -> None:
        from .signals import connect_signals

        connect_signals()

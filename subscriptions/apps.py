from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "subscriptions"

    def ready(self):
        from . import checks  # noqa: F401  (registers deployment checks)
        from .signals import connect_signals
        connect_signals()

from django.apps import AppConfig


class MailcenterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "mailcenter"
    verbose_name = "مركز البريد"

    def ready(self):
        from . import checks  # noqa: F401

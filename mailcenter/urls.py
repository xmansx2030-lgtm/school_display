from django.urls import path

from . import webhook_views


app_name = "mailcenter"

urlpatterns = [
    path("resend/webhook/", webhook_views.resend_webhook, name="resend_webhook"),
]

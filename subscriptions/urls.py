# subscriptions/urls.py
from django.urls import path
from dashboard import views as dashboard_views
from . import moyasar_views, verification_views

app_name = "subscriptions"

urlpatterns = [
    path(
        "email/resend-verification/",
        verification_views.resend_email_verification,
        name="resend_email_verification",
    ),
    path("moyasar/start/", moyasar_views.moyasar_start, name="moyasar_start"),
    path(
        "moyasar/checkout/<str:reference>/",
        moyasar_views.moyasar_checkout,
        name="moyasar_checkout",
    ),
    path(
        "moyasar/checkout/<str:reference>/cancel/",
        moyasar_views.moyasar_cancel,
        name="moyasar_cancel",
    ),
    path("moyasar/return/", moyasar_views.moyasar_return, name="moyasar_return"),
    path("moyasar/webhook/", moyasar_views.moyasar_webhook, name="moyasar_webhook"),
    path("", dashboard_views.system_subscriptions_list, name="system_subscriptions_list"),
    path("add/", dashboard_views.system_subscription_create, name="system_subscription_create"),
    path("<int:pk>/edit/", dashboard_views.system_subscription_edit, name="system_subscription_edit"),
    path("<int:pk>/delete/", dashboard_views.system_subscription_delete, name="system_subscription_delete"),
]

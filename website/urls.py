from django.urls import path
from django.views.generic import TemplateView
from . import views

app_name = "website"

urlpatterns = [
    # Service Worker (اختياري) — لمنع 404 عند طلب /sw.js من المتصفح
    path(
        "sw.js",
        TemplateView.as_view(template_name="sw.js", content_type="application/javascript"),
        name="sw_js",
    ),

    # الصفحة الرئيسية الفعلية (تعرض الشاشة حسب token/short_code في querystring)
    path("", views.home, name="home"),

    # صفحة الاشتراكات العامة (Landing)
    path("subscriptions-page/", views.subscriptions, name="subscriptions"),

    # إنشاء حساب تجربة مجانية من صفحة الهبوط
    path("trial/start/", views.trial_signup, name="trial_signup"),

    # توثيق البريد الإلكتروني عبر رابط موقّع
    path("verify-email/<str:token>/", views.verify_email, name="verify_email"),

    # استكمال طلب باقة مدفوعة بعد الدخول أو إنشاء الحساب
    path("plans/order/", views.plan_order, name="plan_order"),

    # Health check
    path("health/", views.health, name="health"),

    # ربط التلفاز بالجوال عبر رمز مؤقت وQR
    path("tv/", views.tv_pairing, name="tv_pairing"),
    path("tv/pair/start/", views.tv_pairing_start, name="tv_pairing_start"),
    path(
        "tv/pair/<uuid:pairing_id>/status/",
        views.tv_pairing_status,
        name="tv_pairing_status",
    ),
    path(
        "tv/pair/<uuid:pairing_id>/qr/",
        views.tv_pairing_qr,
        name="tv_pairing_qr",
    ),
    path("connect/", views.pairing_connect, name="pairing_connect"),

    # ✅ رابط مختصر (يدعم مع وبدون slash لتفادي 404)
    path("s/<str:short_code>/", views.short_display_redirect, name="short_display"),
    path("s/<str:short_code>", views.short_display_redirect),

    # (اختياري) إذا عندك استخدام داخلي
    # path("display/", views.display, name="display"),
]

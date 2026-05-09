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

    # Health check
    path("health/", views.health, name="health"),

    # ✅ رابط مختصر (يدعم مع وبدون slash لتفادي 404)
    path("s/<str:short_code>/", views.short_display_redirect, name="short_display"),
    path("s/<str:short_code>", views.short_display_redirect),

    # (اختياري) إذا عندك استخدام داخلي
    # path("display/", views.display, name="display"),
]

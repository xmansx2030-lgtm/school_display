from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import path, include, reverse
from django.conf import settings
from django.conf.urls.static import static
from django.template.loader import render_to_string
from django.utils import timezone

from core.static_assets import build_static_response
from core.csp import csp_report


handler403 = "core.error_views.permission_denied"


def ws_display_http_fallback(request):
    """Return 400 instead of 404 when WebSocket endpoint is hit over plain HTTP.

    This happens when a reverse-proxy (e.g. Cloudflare) strips the Upgrade
    header, so the ASGI ProtocolTypeRouter classifies the request as HTTP and
    Django's URL router handles it.  Returning 400 silences the noisy
    '[WARNING] Not Found: /ws/display/' log entries and signals the root cause.
    """
    return JsonResponse(
        {"error": "websocket_required", "detail": "This endpoint requires a WebSocket connection. "
         "If you see this, your proxy may be stripping the Upgrade header."},
        status=400,
    )


def favicon(request):
    """Serve the platform PNG logo without redirect or streaming."""
    response = build_static_response(
        "img/school-display-logo-mark-white-preview (1).png",
        method=request.method,
        cache_control="public, max-age=31536000, immutable",
        is_versioned=True,
    )
    return response or HttpResponse(status=404)


def robots_txt(request):
    """Serve robots.txt safely in dev/test/prod without import-time static URL resolution."""
    loaded = build_static_response(
        "robots.txt",
        method=request.method,
        cache_control="public, max-age=86400",
    )
    sitemap_url = request.build_absolute_uri("/sitemap.xml")
    base_text = "User-agent: *\nAllow: /\n"

    if loaded is not None:
        raw = b""
        try:
            raw = loaded.content or b""
        except Exception:
            raw = b""
        try:
            base_text = raw.decode("utf-8").strip() or base_text.strip()
        except Exception:
            base_text = base_text.strip()

    if "sitemap:" not in base_text.lower():
        base_text = f"{base_text.rstrip()}\nSitemap: {sitemap_url}\n"

    body = b"" if request.method == "HEAD" else base_text.encode("utf-8")
    resp = HttpResponse(body, content_type="text/plain; charset=utf-8")
    resp["Content-Length"] = str(len(base_text.encode("utf-8")))
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


def service_worker(request):
    """Serve the root service worker requested by browsers with old registrations."""
    content = render_to_string("sw.js")
    body = b"" if request.method == "HEAD" else content.encode("utf-8")
    resp = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    resp["Content-Length"] = str(len(content.encode("utf-8")))
    resp["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def sitemap_xml(request):
    """Serve a tiny sitemap for public pages to stop crawler 404 noise."""
    lastmod = timezone.localdate().isoformat()
    urls = [
        (request.build_absolute_uri(reverse("website:home")), "daily", "1.0"),
        (request.build_absolute_uri(reverse("website:subscriptions")), "weekly", "0.8"),
    ]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, changefreq, priority in urls:
        lines.extend(
            [
                "  <url>",
                f"    <loc>{loc}</loc>",
                f"    <lastmod>{lastmod}</lastmod>",
                f"    <changefreq>{changefreq}</changefreq>",
                f"    <priority>{priority}</priority>",
                "  </url>",
            ]
        )
    lines.append("</urlset>")

    content = "\n".join(lines)
    body = b"" if request.method == "HEAD" else content.encode("utf-8")
    resp = HttpResponse(body, content_type="application/xml; charset=utf-8")
    resp["Content-Length"] = str(len(content.encode("utf-8")))
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


urlpatterns = [
    path("csp-report/", csp_report, name="csp_report"),
    path("cpanel-123/", admin.site.urls),

    # favicon (serve directly; avoids /static/* and avoids streaming under ASGI)
    path("favicon.ico", favicon, name="favicon"),
    path("favicon.png", favicon, name="favicon_png"),

    # robots.txt (serve directly to avoid startup/runtime failures in test/dev)
    path("robots.txt", robots_txt, name="robots_txt"),

    # Service worker aliases (satisfy stale browser/PWA registrations without 404 noise)
    path("service-worker.js", service_worker, name="service_worker"),
    path("sw.js", service_worker, name="sw_js"),

    # sitemap.xml (prevents noisy 404s from crawlers and helps indexing)
    path("sitemap.xml", sitemap_xml, name="sitemap_xml"),

    # WebSocket HTTP fallback (prevents 404 noise when proxy strips Upgrade header)
    path("ws/display/", ws_display_http_fallback, name="ws_display_fallback"),

    # موقع الويب (يشمل / و /subscriptions-page/ و /s/<code>)
    path("", include(("website.urls", "website"), namespace="website")),

    # لوحة التحكم
    path("dashboard/", include(("dashboard.urls", "dashboard"), namespace="dashboard")),

    # API Root
    path("api/", include(("core.api_urls", "core_api"), namespace="core_api")),

    # Schedule (لو عندك صفحات غير API)
    path("schedule/", include(("schedule.urls", "schedule"), namespace="schedule")),

]


# ✅ تضمين الاشتراكات إذا كان أي تطبيق يبدأ بـ 'subscriptions' في INSTALLED_APPS
if any(app.startswith("subscriptions") for app in settings.INSTALLED_APPS):
    urlpatterns += [
        path("subscriptions/", include(("subscriptions.urls", "subscriptions"), namespace="subscriptions")),
    ]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

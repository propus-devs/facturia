from django.contrib import admin
from django.urls import path, include
from django.contrib.auth.views import LoginView, LogoutView
from django.views.generic import RedirectView
from django.conf import settings
from django.http import JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import cache_control
from documents.views import DashboardView, MetricsDashboardView


@cache_control(max_age=86400, public=True)
def web_manifest(request):
    """Manifest para poder instalar la web como acceso directo (PWA sin service worker)."""
    data = {
        "name": "FacturIA",
        "short_name": "FacturIA",
        "description": "Gestión de facturas y movimientos financieros",
        "id": "/",
        "start_url": "/dashboard/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#050816",
        "theme_color": "#050816",
        "lang": "es",
        "icons": [
            {"src": static("favicons/android-chrome-192x192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static("favicons/android-chrome-512x512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static("favicons/maskable-512x512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    return JsonResponse(data, content_type="application/manifest+json")


urlpatterns = [
    path("manifest.webmanifest", web_manifest, name="web_manifest"),
    path("", RedirectView.as_view(pattern_name="dashboard"), name="home"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path('login/', LoginView.as_view(template_name='public/auth/login.html', redirect_authenticated_user=True), name='login'),
    path('logout/', LogoutView.as_view(next_page='home'), name='logout'),
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),
    path('documents/', include("documents.urls")),
    path('metrics/dashboard/', MetricsDashboardView.as_view(), name='metrics'),
    path('finance/', include("finance.urls")),
    path('clients/', include("clients.urls")),
]

if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    from debug_toolbar.toolbar import debug_toolbar_urls
    urlpatterns += debug_toolbar_urls()




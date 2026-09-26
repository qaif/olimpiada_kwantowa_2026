"""Urlconf djcms. **Kolejność jest regułą bezpieczeństwa** (reguła 5 z § 7 DJ-01, S5/S6 DJ-02).

Wszystkie adresy aplikacyjne djcms stoją pod jednym prefiksem ``/djcms/`` (DJ-02 D2): djcms
odpowiada na tych samych hostach co aplikacja główna, więc ``/admin/``, ``/static/`` i ``/media/``
są zajęte przez ``web``. W korzeniu zostają tylko ``robots.txt``, ``sitemap.xml`` i drzewo stron.

``cms.urls`` łapie każdą ścieżkę (``pages-details-by-slug``), więc wszystko, co należy do
aplikacji, a nie do redaktora, musi stać **przed** nim. Pilnuje tego test
``apps/pages/tests/test_urls_and_checks.py``; ścieżki zarezerwowane – ``settings.DJ_RESERVED_SLUGS``
i ``apps.pages.validation``.

``/djcms/static/`` obsługuje WhiteNoise (middleware – przed urlconfem), ``/djcms/media/`` na
produkcji Caddy (wolumen ``djcms_media``), w devie ``static()`` niżej.

Strony błędów: 404 z ramą konkursu (``apps.pages.views.page_not_found``); 500 – domyślny widok
Django ze statycznym ``templates/500.html`` (bez kontekstu, bez API i bez zapytań).
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.pages.views import healthz, preview_toggle, robots_txt, sitemap_xml
from apps.sites.views import sso_login

djcms_patterns = [
    path("healthz/", healthz, name="healthz"),
    # Włączenie/wyłączenie podglądu – ciasteczko ``djcms_view`` na hoście konkursu (DJ-02 D1).
    path("preview/", preview_toggle, name="preview"),
    # Logowanie redaktorów tokenem z ``/cms/`` aplikacji głównej (SSO, DJ-02 D6) – wyłącznie ``POST``.
    path("sso/", sso_login, name="sso"),
    path("admin/", admin.site.urls),
]

urlpatterns = [
    path("djcms/", include(djcms_patterns)),
    path("robots.txt", robots_txt, name="robots-txt"),
    path("sitemap.xml", sitemap_xml, name="sitemap-xml"),
]

if settings.DEBUG:  # pragma: no cover - tylko dev (runserver); produkcja: Caddy file_server
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Zawsze ostatni – patrz docstring modułu.
urlpatterns.append(path("", include("cms.urls")))

handler404 = "apps.pages.views.page_not_found"

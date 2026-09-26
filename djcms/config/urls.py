"""Urlconf djcms. **Kolejność jest regułą bezpieczeństwa** (reguła 5 z § 7 DJ-01, S5/S6 DJ-02).

Wszystkie adresy aplikacyjne djcms stoją pod jednym prefiksem ``/djcms/`` (DJ-02 D2): djcms
odpowiada na tych samych hostach co aplikacja główna, więc ``/admin/``, ``/static/`` i ``/media/``
są zajęte przez ``web``. W korzeniu zostają tylko ``robots.txt`` i drzewo stron.

``cms.urls`` łapie każdą ścieżkę (``pages-details-by-slug``), więc wszystko, co należy do
aplikacji, a nie do redaktora, musi stać **przed** nim. Pilnuje tego test
``apps/pages/tests/test_urls_and_checks.py``; ścieżki zarezerwowane – ``settings.DJ_RESERVED_SLUGS``
i ``apps.pages.validation``.

``/djcms/static/`` obsługuje WhiteNoise (middleware – przed urlconfem), ``/djcms/media/`` na
produkcji Caddy (wolumen ``djcms_media``), w devie ``static()`` niżej.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.pages.views import healthz, not_yet_available, robots_txt

djcms_patterns = [
    path("healthz/", healthz, name="healthz"),
    # Włączenie/wyłączenie podglądu (``djcms_view``, DJ-02f) i logowanie redaktorów z ``/cms/``
    # (SSO, DJ-02g). Adresy są zarezerwowane już teraz – odpowiadają pustą 404 do czasu tych kroków.
    path("preview/", not_yet_available, name="preview"),
    path("sso/", not_yet_available, name="sso"),
    path("admin/", admin.site.urls),
]

urlpatterns = [
    path("djcms/", include(djcms_patterns)),
    path("robots.txt", robots_txt, name="robots-txt"),
]

if settings.DEBUG:  # pragma: no cover - tylko dev (runserver); produkcja: Caddy file_server
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Zawsze ostatni – patrz docstring modułu.
urlpatterns.append(path("", include("cms.urls")))

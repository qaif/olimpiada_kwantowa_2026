"""Urlconf djcms. **Kolejność jest regułą bezpieczeństwa** (reguła 5 z § 7 docs/tasks/DJ-01.md).

``cms.urls`` łapie każdą ścieżkę (``pages-details-by-slug``), więc wszystko, co należy do
aplikacji, a nie do redaktora, musi stać **przed** nim: inaczej strona o slugu ``admin``
albo ``healthz`` przesłoniłaby panel albo healthcheck. Pilnuje tego test
``apps/pages/tests/test_urls.py``; slugi zarezerwowane są w ``settings.DJ_RESERVED_SLUGS``.

``/static/`` obsługuje WhiteNoise (middleware – przed urlconfem), ``/media/`` na produkcji Caddy
(wolumen ``djcms_media``), w devie ``static()`` niżej.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.pages.views import healthz, robots_txt

urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("robots.txt", robots_txt, name="robots-txt"),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:  # pragma: no cover - tylko dev (runserver); produkcja: Caddy file_server
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Zawsze ostatni – patrz docstring modułu.
urlpatterns.append(path("", include("cms.urls")))

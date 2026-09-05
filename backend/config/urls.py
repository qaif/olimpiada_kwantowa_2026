"""Mapa adresów projektu.

Kolejność jest kontraktem (T-09): Wagtail montuje się jako **catch-all** w korzeniu, więc wszystko,
co ma własną obsługę – panel admina, ``/healthz/``, API, admin Wagtaila (``/cms/``), dokumenty
(``/documents/``) i interfejs WWW (``apps.web``) – musi być dopasowane wcześniej. Ostatni wpis
oddaje resztę drzewu stron CMS; nietrafiony adres kończy się tam 404 z ``wagtail.views.serve``.
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls
from wagtail.documents import urls as wagtaildocs_urls

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", include("apps.core.urls")),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/competitions/", include("apps.competitions.urls")),
    path("api/", include("apps.submissions.urls")),
    path("api/grading/", include("apps.grading.urls")),
    path("api/", include("apps.appeals.urls")),
    path("api/", include("apps.results.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Redakcja części informacyjnej. Dostęp ma wyłącznie grupa `coordinator` (migracja cms.0003).
    path("cms/", include(wagtailadmin_urls)),
    # Dokumenty Wagtaila serwuje widok aplikacji, a nie bezpośredni URL bucketu.
    path("documents/", include(wagtaildocs_urls)),
    # Interfejs WWW montowany w korzeniu – zawsze po prefiksach API, żeby nie przechwycił /api/.
    path("", include("apps.web.urls")),
]

if settings.DEBUG:
    # Dev bez proxy: obrazy Wagtaila (FileSystemStorage) muszą być osiągalne spod MEDIA_URL.
    # W produkcji media stoją na S3/MinIO, więc ten wpis nigdy się nie pojawia.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Catch-all: drzewo stron Wagtaila. MUSI zostać ostatni.
urlpatterns += [path("", include(wagtail_urls))]

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
from drf_spectacular.views import SpectacularAPIView
from wagtail import urls as wagtail_urls
from wagtail.admin import urls as wagtailadmin_urls

from apps.web.views.docs import NonceSwaggerView

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
    # Swagger UI z nonce'ami i SRI – patrz apps/web/views/docs.py. Wersja biblioteki podaje
    # szablonowi wyłącznie adresy plików, więc podmiana widoku jest jedynym miejscem, w którym
    # da się dołożyć skróty SRI bez rozjazdu z pinowaną wersją w ustawieniach.
    path("api/docs/", NonceSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Redakcja części informacyjnej. Dostęp ma wyłącznie grupa `coordinator` (migracja cms.0003).
    path("cms/", include(wagtailadmin_urls)),
    # Dokumenty Wagtaila serwuje widok aplikacji, a nie bezpośredni URL bucketu. Wzorce są kopią
    # ``wagtail.documents.urls`` z jedną zmianą: widok dokłada ``Cache-Control`` plikom z kolekcji
    # bez ograniczeń widoczności – patrz apps/cms/views.py.
    path("documents/", include("apps.cms.documents_urls")),
    # Interfejs WWW montowany w korzeniu – zawsze po prefiksach API, żeby nie przechwycił /api/.
    path("", include("apps.web.urls")),
    # Logowanie przez Google/Facebooka. **Za** ``apps.web.urls``, bo jest tam wzorzec ``login/``
    # istniejący wyłącznie po to, żeby ``reverse("account_login")`` allauth dawało ``/login/`` –
    # samo dopasowanie adresu ma nadal trafiać do ``web:login``. Przed catch-allem Wagtaila,
    # inaczej ``/accounts/…`` skończyłoby na drzewie stron CMS.
    path("", include("apps.web.social_urls")),
]

if settings.DEBUG:
    # Dev bez proxy: obrazy Wagtaila (FileSystemStorage) muszą być osiągalne spod MEDIA_URL.
    # W produkcji media stoją na S3/MinIO, więc ten wpis nigdy się nie pojawia.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Catch-all: drzewo stron Wagtaila. MUSI zostać ostatni.
urlpatterns += [path("", include(wagtail_urls))]

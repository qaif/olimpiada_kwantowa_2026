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
from apps.web.views.public import site_verification
from apps.web.views.statistics import StatisticsView
from apps.web.views.status import StatusJsonView, StatusView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz/", include("apps.core.urls")),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/competitions/", include("apps.competitions.urls")),
    path("api/schools/", include("apps.schools.urls")),
    path("api/", include("apps.submissions.urls")),
    path("api/grading/", include("apps.grading.urls")),
    path("api/", include("apps.appeals.urls")),
    path("api/", include("apps.results.urls")),
    # Publiczne API dla systemów zewnętrznych. Numer wersji jest w **prefiksie**, a nie w nagłówku:
    # adres integracji da się wpisać do konfiguracji partnera, wkleić do zgłoszenia i odczytać
    # z logu proxy. Uwierzytelnia klucz API (``apps.integrations.auth``), nie konto – dlatego stoi
    # osobno od reszty ``/api/…``, choć schemat i Swagger są wspólne.
    path("api/v1/", include("apps.integrations.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    # Swagger UI z nonce'ami i SRI – patrz apps/web/views/docs.py. Wersja biblioteki podaje
    # szablonowi wyłącznie adresy plików, więc podmiana widoku jest jedynym miejscem, w którym
    # da się dołożyć skróty SRI bez rozjazdu z pinowaną wersją w ustawieniach.
    path("api/docs/", NonceSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    # Obrazek CAPTCHY publicznych formularzy rejestracji (django-simple-captcha). Adres jest
    # nasz i dlatego ``img-src 'self'`` z CSP wystarcza – nic tu nie przychodzi z obcej domeny.
    # Musi stać przed catch-allem Wagtaila, inaczej obrazek szukałby się w drzewie stron.
    path("captcha/", include("captcha.urls")),
    # Pliki potwierdzające własność domeny (Google Search Console). Przed trasami Wagtaila, bo te
    # zjadłyby adres jako nieistniejącą stronę i odpowiedziały 404.
    path("<str:token>.html", site_verification, name="site-verification"),
    # Przełącznik języka Django (``/i18n/setlang/``). Zamontowany dla zgodności z biblioteką
    # i dla klientów, które go znają; własny formularz w pasku konta idzie na
    # ``/account/preferences/``, bo zapisuje **dwie** rzeczy naraz (język i kontrast).
    path("i18n/", include("django.conf.urls.i18n")),
    # Statystyki ogłoszonych etapów. Widok aplikacji, nie strona w CMS-ie: treść jest w całości
    # wyliczana z publikacji wyników, a redaktor nie ma w niej niczego do napisania. Musi stać
    # przed catch-allem Wagtaila – tak samo, jak ``/results/<id>/`` w ``apps.web.urls``.
    path("statystyki/", StatisticsView.as_view(), name="statistics"),
    # Strona statusu serwisu i jej wariant maszynowy. Publiczne, buforowane na 30 sekund.
    # Osobny adres dla JSON-a, a nie ``?format=json``: monitory zewnętrzne konfiguruje się
    # adresem, a część z nich rozpoznaje typ odpowiedzi po rozszerzeniu. Oba wzorce muszą stać
    # **przed** catch-allem Wagtaila – inaczej skończyłyby na drzewie stron jako 404. To co innego
    # niż ``/healthz/``: tamto odpowiada orkiestratorowi kodem HTTP, to – człowiekowi treścią.
    path("status/", StatusView.as_view(), name="status"),
    path("status.json", StatusJsonView.as_view(), name="status-json"),
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

"""Ustawienia wspólne wersji porównawczej na django CMS (``dj.<SITE_DOMAIN>``, docs/tasks/DJ-01.md).

To jest **osobny** projekt Django: własna baza (``olimpiada_djcms``), własna rola Postgresa, własni
użytkownicy i własny sekret. Z aplikacją główną łączy go wyłącznie wewnętrzne API tylko do odczytu
(``DJCMS_MAIN_API_URL`` + ``DJCMS_INTERNAL_TOKEN``) i wspólne pliki statyczne (``backend/static``,
montowane albo kopiowane do ``DJCMS_SHARED_STATIC_DIR``). Nigdy baza aplikacji głównej ani MinIO –
dlatego żadna zmienna tego pliku nie nazywa się tak jak zmienna backendu (``DJANGO_SECRET_KEY``,
``POSTGRES_*``): usługa compose nie dostaje ``env_file: .env``, tylko jawną listę zmiennych.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()

# ``DJCMS_BUILD=1`` ustawia wyłącznie Dockerfile na czas ``collectstatic``: pozwala wczytać
# ustawienia produkcyjne bez sekretu i bez bazy (obraz nie może nosić sekretu, a w czasie budowania
# bazy nie ma). Żaden proces obsługujący żądania nie startuje z tą flagą.
BUILDING_IMAGE = env.bool("DJCMS_BUILD", default=False)

SECRET_KEY = env("DJCMS_SECRET_KEY", default="")
DEBUG = env.bool("DJCMS_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJCMS_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJCMS_CSRF_TRUSTED_ORIGINS", default=[])
# Adresy z ``X-Real-IP`` honorujemy wyłącznie od tych proxy (Caddy) – ta sama reguła co w backendzie
# (``apps.core.models.client_ip``); używa jej blokada prób logowania (``apps.pages.auth``).
TRUSTED_PROXY_IPS = env.list("TRUSTED_PROXY_IPS", default=[])

INSTALLED_APPS = [
    # Musi stać przed ``django.contrib.admin`` – nadpisuje jego szablony (ekstra ``admin-style``).
    "djangocms_simple_admin_style",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "cms",
    "menus",
    "treebeard",
    "sekizai",
    "djangocms_versioning",
    "djangocms_text",
    "filer",
    "easy_thumbnails",
    "apps.pages",
    # Klient API aplikacji głównej i rama z endpointu ``chrome`` (DJ-01d); wtyczki żywe – DJ-01f.
    "apps.live",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Oba nasze middleware stoją **na zewnątrz** wszystkiego, co może odpowiedzieć samo
    # (WhiteNoise, CSRF, widok, konwersja wyjątku na 500): nagłówki CSP i ``X-Robots-Tag`` mają
    # dostać także pliki statyczne, odmowy CSRF i strony błędów.
    "apps.pages.middleware.ContentSecurityPolicyMiddleware",
    "apps.pages.middleware.NoIndexMiddleware",
    # ``X-Djcms-Degraded: 1`` na stronie złożonej bez świeżych danych z API (§ 8.3) – nagłówek
    # czyta dopiero po wyrenderowaniu szablonu, więc wystarczy, że stoi na zewnątrz widoku.
    "apps.live.middleware.DegradedHeaderMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Wymagane przez ``cms check`` (lista w ``cms/utils/check.py`` 5.1.3), mimo jednego języka.
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Middleware django CMS 5.1.3 (ta sama lista co ``check_middlewares`` w ``cms/utils/check.py``).
    "cms.middleware.user.CurrentUserMiddleware",
    "cms.middleware.page.CurrentPageMiddleware",
    "cms.middleware.toolbar.ToolbarMiddleware",
    "cms.middleware.language.LanguageCookieMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "cms.context_processors.cms_settings",
                "sekizai.context_processors.sekizai",
                # ``dj_chrome`` – leniwe dane ramy z API (nie pyta API, dopóki szablon nie sięgnie).
                "apps.live.context_processors.chrome",
            ],
        },
    }
]

# --- Baza ------------------------------------------------------------------------------------
# ``CONN_MAX_AGE = 0`` bez wyjątków: nauczka z incydentu 09.09.2026 (bezczynne połączenia
# trzymane przez procesy aplikacji, docs/OPERACJE.md § 11.2). Budżet djcms to ≤ 4 połączenia
# (gunicorn 2 workery × 2 wątki). Domyślny URL działa tylko w czasie budowania obrazu, gdzie
# baza i tak nie jest potrzebna – proces obsługujący żądania dostaje ``DATABASE_URL`` z compose'a.
DATABASES = {
    "default": env.db_url("DATABASE_URL", default="postgres://olimpiada_djcms@localhost:5432/olimpiada_djcms")
}
DATABASES["default"]["CONN_MAX_AGE"] = 0
# Nazwa w ``pg_stat_activity`` – po niej ``manage.py db_connections`` aplikacji głównej i list
# alarmowy mówią, że połączenia trzyma djcms, a nie ``web``.
DATABASES["default"].setdefault("OPTIONS", {})["application_name"] = "olimpiada-djcms"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Bufor -----------------------------------------------------------------------------------
# ``default`` – LocMem, świadomie bez Redisa: to bufor odpowiedzi API (per proces, 60 s + kopia
# „stale”, § 8.3) i menu django CMS. Djcms nie dostaje dostępu do Redisa aplikacji głównej.
#
# ``throttle`` – licznik nieudanych logowań (``apps.pages.auth``). **Plikowy w ``/tmp``**, a nie
# LocMem: gunicorn ma dwa procesy i przy liczniku w pamięci procesu limit „5 prób / 15 min”
# byłby w praktyce limitem „5 prób na proces”, czyli 10. ``/tmp`` jest w kontenerze ``tmpfs``
# (``read_only: true`` zostaje), wspólnym dla obu procesów, i znika z restartem – blokada
# prób logowania nie jest stanem, który ma przetrwać wdrożenie.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "djcms-default"},
    "throttle": {
        "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
        "LOCATION": env("DJCMS_THROTTLE_CACHE_DIR", default="/tmp/djcms-throttle"),  # noqa: S108
    },
}

# --- Uwierzytelnienie ------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = ["apps.pages.auth.ThrottledModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# Reguła 13 (§ 7): 5 nieudanych prób na parę (IP, login) w oknie 15 minut.
DJCMS_LOGIN_MAX_FAILURES = env.int("DJCMS_LOGIN_MAX_FAILURES", default=5)
DJCMS_LOGIN_WINDOW_SECONDS = env.int("DJCMS_LOGIN_WINDOW_SECONDS", default=15 * 60)
LOGIN_URL = "admin:login"

# Ciasteczka: **inne nazwy** niż w aplikacji głównej i bez ``*_COOKIE_DOMAIN`` (host-only).
# Gdyby domena główna kiedyś ustawiła ciasteczko na ``.olimpiadakwantowa.pl``, trafiałoby ono
# także na ``dj.`` – różne nazwy wykluczają, że którakolwiek aplikacja weźmie cudzą sesję za swoją.
SESSION_COOKIE_NAME = "djcms_sessionid"
CSRF_COOKIE_NAME = "djcms_csrftoken"
SESSION_COOKIE_HTTPONLY = True
# Skrypty django CMS, filera i versioningu biorą token z formularza (``csrfmiddlewaretoken``) albo
# z ``CMS.config.csrf``, a ciasteczko czytają tylko awaryjnie – i to pod domyślną nazwą
# ``csrftoken``, której tu i tak nie ma. ``HttpOnly`` niczego im więc nie odbiera.
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Ciasteczko języka ustawia ``cms.middleware.language.LanguageCookieMiddleware`` na każdej odpowiedzi
# (także anonimowi). Ta sama zasada co wyżej: własna nazwa, host-only, bez dostępu ze skryptów.
LANGUAGE_COOKIE_NAME = "djcms_language"
LANGUAGE_COOKIE_HTTPONLY = True
LANGUAGE_COOKIE_SAMESITE = "Lax"
# ``SAMEORIGIN``, nie ``DENY``: tryb struktury i okna modalne django CMS osadzają admina
# w ``<iframe>`` tej samej domeny. CSP publicznych stron i tak mówi ``frame-ancestors 'none'``.
X_FRAME_OPTIONS = "SAMEORIGIN"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Język i czas ----------------------------------------------------------------------------
# Jeden język i **bez** ``i18n_patterns``: adresy na ``dj.`` mają być te same co na domenie
# głównej (``/zadania/``, a nie ``/pl/zadania/``) – inaczej porównanie stron obok siebie
# i odnośniki względne z importu (§ 3.1, ``api_href``) nie miałyby sensu.
LANGUAGE_CODE = "pl"
LANGUAGES = [("pl", "Polski")]
TIME_ZONE = "Europe/Warsaw"
USE_I18N = True
USE_TZ = True
SITE_ID = 1

# --- django CMS 5.1.3 ------------------------------------------------------------------------
# ``CMS_CONFIRM_VERSION4`` nie istnieje już w 5.1.3 (brak w ``cms/utils/conf.py``) – nie ustawiamy.
CMS_LANGUAGES = {
    SITE_ID: [
        {
            "code": "pl",
            "name": "Polski",
            "public": True,
            "hide_untranslated": False,
            "redirect_on_fallback": True,
        }
    ],
    "default": {"public": True, "hide_untranslated": False, "redirect_on_fallback": True},
}
# Szablony stron. Pełna lista (typy z tabeli 6.1) przychodzi w DJ-01e; na etapie szkieletu jest
# jeden szablon, bo django CMS odmawia działania z pustym ``CMS_TEMPLATES``.
CMS_TEMPLATES = [("dj/pages/content.html", "Strona treści")]
# Bufor całych stron **wyłączony**: HTML niesie jednorazowy nonce CSP i dane zawodów z API
# (terminy, stan rejestracji) – strona z bufora miałaby cudzy nonce (skrypty zablokowane) albo
# nieaktualny termin. Bufor placeholderów zostaje, bo wtyczki żywe mają ``cache = False``.
CMS_PAGE_CACHE = False
CMS_PLACEHOLDER_CACHE = True
# Uprawnienia per strona wyłączone – jedna grupa „Redaktorzy” (decyzja z 26.09.2026) na zwykłych
# uprawnieniach modeli Django (``manage.py setup_djcms_groups``).
CMS_PERMISSION = False
# Pasek narzędzi **nie** dla anonimów: jego skrypty wymagają luźnej polityki CSP, a publiczne
# strony mają ścisłą. Redaktor loguje się pod ``/admin/`` i dopiero wtedy dostaje pasek.
CMS_TOOLBAR_ANONYMOUS_ON = False

# djangocms-versioning 2.7.1: skasowanie wersji (także opublikowanej) wyłączone – to jest cała
# historia treści, odpowiednik rewizji Wagtaila. Wartość domyślna, wpisana jawnie.
DJANGOCMS_VERSIONING_ALLOW_DELETING_VERSIONS = False

# djangocms-text 1.0.1: sanityzacja HTML (``nh3``) przy zapisie – reguła 4 z § 7. Domyślnie
# włączona; wpisana jawnie, bo wyłączenie jej jest dokładnie tym, czego tu nie wolno.
TEXT_HTML_SANITIZE = True

# --- Pliki (filer) ---------------------------------------------------------------------------
MEDIA_URL = "/media/"
MEDIA_ROOT = env("DJCMS_MEDIA_ROOT", default="/app/media")
# Wszystkie pliki filera są publiczne (reguła 12 – ``/media/*`` serwuje Caddy wprost z wolumenu).
# Uprawnienia per folder wyłączone: redaktorzy są jedną grupą, a pliki i tak są dostępne pod
# jawnym adresem. Flaga „prywatny” w filerze nie jest więc granicą bezpieczeństwa – DJ-01e ma
# ją ukryć albo zablokować, zanim redaktorzy zaczną wgrywać pliki.
FILER_ENABLE_PERMISSIONS = False
FILER_IS_PUBLIC_DEFAULT = True
THUMBNAIL_PROCESSORS = (
    "easy_thumbnails.processors.colorspace",
    "easy_thumbnails.processors.autocrop",
    "filer.thumbnail_processors.scale_and_crop_with_subject_location",
    "easy_thumbnails.processors.filters",
)
THUMBNAIL_HIGH_RESOLUTION = True

# --- Pliki statyczne -------------------------------------------------------------------------
# Te same arkusze/skrypty/fonty co aplikacja główna (§ 1.2 p. 6): obraz kopiuje ``backend/static``
# do ``/opt/shared_static``, dev montuje ten sam katalog. Brak kopii w repozytorium = brak rozjazdu.
# ``djcms/static`` dochodzi tylko wtedy, gdy istnieje (docelowo pusty – tylko to, czego nie ma
# w ``backend/static``). Katalog współdzielony jest na liście zawsze: jego brak ma dać ostrzeżenie
# ``staticfiles.W004``, a nie cichą stronę bez stylów.
STATIC_URL = "/static/"
STATIC_ROOT = env("DJCMS_STATIC_ROOT", default="/app/staticfiles")
STATICFILES_DIRS = [
    *([BASE_DIR / "static"] if (BASE_DIR / "static").is_dir() else []),
    env("DJCMS_SHARED_STATIC_DIR", default="/opt/shared_static"),
]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# --- Aplikacja główna (API wewnętrzne, § 8.3) --------------------------------------------------
# Klient: ``apps/live/client.py``. ``DJCMS_API_TIMEOUT`` to limit **całego** żądania; pojedyncza
# operacja gniazda (połączenie, odczyt) ma najwyżej 1 s (``client.CONNECT_TIMEOUT_SECONDS``).
DJCMS_MAIN_API_URL = env("DJCMS_MAIN_API_URL", default="http://web:8000/internal/djcms/v1/")
DJCMS_INTERNAL_TOKEN = env("DJCMS_INTERNAL_TOKEN", default="")
DJCMS_MAIN_PUBLIC_URL = env("DJCMS_MAIN_PUBLIC_URL", default="http://localhost:8000")
# Origin publicznego kubełka mediów głównego serwisu (logo organizatora, slider sponsorów) –
# trafia do ``img-src``/``media-src`` polityki CSP. Pusty = pominięty.
DJCMS_MAIN_MEDIA_ORIGIN = env("DJCMS_MAIN_MEDIA_ORIGIN", default="")
DJCMS_API_TIMEOUT = env.float("DJCMS_API_TIMEOUT", default=2.0)
DJCMS_API_CACHE_SECONDS = env.int("DJCMS_API_CACHE_SECONDS", default=60)
DJCMS_API_STALE_SECONDS = env.int("DJCMS_API_STALE_SECONDS", default=600)
DJCMS_API_BREAKER_SECONDS = env.int("DJCMS_API_BREAKER_SECONDS", default=15)
DJCMS_FALLBACK_SITE_NAME = env("DJCMS_FALLBACK_SITE_NAME", default="Olimpiada Kwantowa")

# Adresy aplikacji djcms, których redaktor nie może przesłonić stroną na poziomie korzenia
# (reguła 5 z § 7). ``config/urls.py`` stawia je przed ``cms.urls``, import odrzuca taki slug,
# a system check ``dj_pages.W001`` wypisuje opublikowane strony, które go mimo to mają.
DJ_RESERVED_SLUGS = frozenset({"admin", "static", "media", "healthz", "robots.txt", "internal", "filer"})

# ``treebeard.E001`` (w praktyce ostrzeżenie): „``cms.models.managers.PageManager`` nie dziedziczy
# po ``MP_NodeManager`` – błąd w Treebeard 6”. To jest dokładnie powód, dla którego django CMS
# 5.1.3 trzyma ``django-treebeard<6`` (pin 5.3.1 w pyproject.toml), więc na tej parze wersji nie
# ma nic do naprawienia po naszej stronie, a ostrzeżenie zaśmiecałoby każdy ``migrate``
# i ``check`` (także w CI). Wyciszenie znika razem z pinem treebeard, gdy django CMS dopuści 6+.
SILENCED_SYSTEM_CHECKS = ["treebeard.E001"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

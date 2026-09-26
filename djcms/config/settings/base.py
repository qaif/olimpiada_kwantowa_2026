"""Ustawienia wspólne serwisu na django CMS (docs/tasks/DJ-01.md; wiele witryn – docs/tasks/DJ-02.md).

Od DJ-02 djcms obsługuje **każdy** konkurs platformy (jedna witryna django CMS na konkurs, bez
``SITE_ID``) na tych samych hostach co aplikacja główna, z adresami aplikacyjnymi pod ``/djcms/``.

To jest **osobny** projekt Django: własna baza (``olimpiada_djcms``), własna rola Postgresa, własni
użytkownicy i własny sekret. Z aplikacją główną łączy go wyłącznie wewnętrzne API tylko do odczytu
(``DJCMS_MAIN_API_URL`` + ``DJCMS_INTERNAL_TOKEN``) i wspólne pliki statyczne (``backend/static``,
montowane albo kopiowane do ``DJCMS_SHARED_STATIC_DIR``). Nigdy baza aplikacji głównej ani MinIO –
dlatego żadna zmienna tego pliku nie nazywa się tak jak zmienna backendu (``DJANGO_SECRET_KEY``,
``POSTGRES_*``): usługa compose nie dostaje ``env_file: .env``, tylko jawną listę zmiennych.
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

# Zestawy wtyczek ART/DOC (tabela 6.1) – moduł bez importów Django, bezpieczny do wczytania tutaj.
from apps.blocks.plugin_sets import ART_PLUGINS, DOC_PLUGINS, TEXT_ONLY

# Kontrakt tras z aplikacją główną (``app_routes.json``, DJ-02 § 6) – moduł bez importów Django.
from apps.pages.contract import ContractError, default_contract_dir, load_app_routes

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()

# ``DJCMS_BUILD=1`` ustawia wyłącznie Dockerfile na czas ``collectstatic``: pozwala wczytać
# ustawienia produkcyjne bez sekretu i bez bazy (obraz nie może nosić sekretu, a w czasie budowania
# bazy nie ma). Żaden proces obsługujący żądania nie startuje z tą flagą.
BUILDING_IMAGE = env.bool("DJCMS_BUILD", default=False)

SECRET_KEY = env("DJCMS_SECRET_KEY", default="")
DEBUG = env.bool("DJCMS_DEBUG", default=False)
# --- Hosty (DJ-02 § 5.4) ---------------------------------------------------------------------
# djcms odpowiada pod **każdym** hostem konkursu (D1: podgląd i tryb PRIMARY na prawdziwych
# domenach), więc listy hostów liczymy z tych samych zmiennych co aplikacja główna
# (``backend/config/settings/base.py``: ``SITE_DOMAIN``, ``EXTRA_DOMAINS``, ``PLATFORM_SUBDOMAINS``),
# plus ``dj.<SITE_DOMAIN>`` (wejście podglądu) i nazwa usługi compose'a (``djcms``).
# ``DJCMS_ALLOWED_HOSTS``/``DJCMS_CSRF_TRUSTED_ORIGINS`` wyłącznie **dokładają** wpisy. Który konkurs
# stoi pod hostem, rozstrzyga rejestr (``apps.sites``) – host wpuszczony tutaj, a nieznany
# rejestrowi, dostaje pustą 404.
SITE_DOMAIN = env("SITE_DOMAIN", default="").strip().lower().rstrip(".")
EXTRA_DOMAINS = [host.lower() for host in env("EXTRA_DOMAINS", default="").split() if host]
PLATFORM_SUBDOMAINS = env.bool("PLATFORM_SUBDOMAINS", default=False)
_domain_hosts = [SITE_DOMAIN, f"dj.{SITE_DOMAIN}"] if SITE_DOMAIN else []
ALLOWED_HOSTS = list(
    dict.fromkeys(
        [
            *env.list("DJCMS_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"]),
            "djcms",
            *_domain_hosts,
            *EXTRA_DOMAINS,
            *([f".{SITE_DOMAIN}"] if PLATFORM_SUBDOMAINS and SITE_DOMAIN else []),
        ]
    )
)
CSRF_TRUSTED_ORIGINS = list(
    dict.fromkeys(
        [
            *env.list("DJCMS_CSRF_TRUSTED_ORIGINS", default=[]),
            *(f"https://{host}" for host in [*_domain_hosts, *EXTRA_DOMAINS] if host != "localhost"),
            *([f"https://*.{SITE_DOMAIN}"] if PLATFORM_SUBDOMAINS and SITE_DOMAIN else []),
        ]
    )
)
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
    # Witryny konkursów: rejestr, rozstrzyganie host/prefiks, łatka ``SiteManager.get_current`` (DJ-02d).
    "apps.sites",
    "apps.pages",
    # Wtyczki redakcyjne – odpowiedniki bloków StreamField Wagtaila (DJ-01e, § 6.2).
    "apps.blocks",
    # Klient API aplikacji głównej i rama z endpointu ``chrome`` (DJ-01d); wtyczki żywe – DJ-01f.
    "apps.live",
    # Import treści z paczki Wagtaila – komenda ``import_cms_bundle`` (DJ-01g, § 5.3).
    "apps.importer",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Witryna konkursu żądania (DJ-02 § 5.3): ``request.site``, ``request.competition_site``, prefiks
    # ścieżki i pusta 404 nieznanego hosta – przed wszystkim, co czyta witrynę albo renderuje ramę.
    # ``CurrentSiteMiddleware`` Django **nie** jest używane: robi to ta warstwa.
    "apps.sites.middleware.CompetitionSiteMiddleware",
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
    # Strona bieżąca pod prefiksem ścieżki konkursu – poprawka ``get_page_from_request`` (apps.sites).
    "apps.sites.middleware.PrefixedCurrentPageMiddleware",
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
# LocMem, świadomie bez Redisa: to bufor odpowiedzi API (per proces, 60 s + kopia „stale”, § 8.3)
# i menu django CMS. Djcms nie dostaje dostępu do Redisa aplikacji głównej. Licznik blokady
# logowania **nie** jest w buforze – ma własną tabelę (``apps.pages.models.LoginAttempt``): bufor
# wyrzuca wpisy po przekroczeniu limitu, a licznik, który da się wyrzucić zalewem śmieciowych
# prób, nie jest blokadą (uzasadnienie w docstringu ``apps.pages.auth``).
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "djcms-default"},
}

# --- Uwierzytelnienie ------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = ["apps.pages.auth.ThrottledModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
# Reguła 13 (§ 7): 5 nieudanych prób na parę (IP, login) w oknie 15 minut, plus sufit 50 porażek
# na login w oknie godziny z dowolnych adresów (zgadywanie rozproszone – ``apps.pages.auth``).
DJCMS_LOGIN_MAX_FAILURES = env.int("DJCMS_LOGIN_MAX_FAILURES", default=5)
DJCMS_LOGIN_WINDOW_SECONDS = env.int("DJCMS_LOGIN_WINDOW_SECONDS", default=15 * 60)
DJCMS_LOGIN_USER_MAX_FAILURES = env.int("DJCMS_LOGIN_USER_MAX_FAILURES", default=50)
DJCMS_LOGIN_USER_WINDOW_SECONDS = env.int("DJCMS_LOGIN_USER_WINDOW_SECONDS", default=60 * 60)
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
# **Bez** ``SITE_ID`` (DJ-02 D4): witryn jest tyle, ile konkursów, a witrynę żądania wyznacza
# ``apps.sites.middleware.CompetitionSiteMiddleware`` (``request.site``) + łatka
# ``SiteManager.get_current`` (``apps.sites.patches``). Wywołanie ``Site.objects.get_current()``
# **bez żądania** kończy się ``ImproperlyConfigured`` – to jest bezpiecznik: kod poza żądaniem
# (komendy, importer) podaje witrynę jawnie.
#
# Skutek uboczny przyjęty świadomie (``cms/utils/conf.py::get_languages`` 5.1.3): bez ``SITE_ID``
# django CMS **ignoruje** ``CMS_LANGUAGES`` i bierze ``LANGUAGES`` – jeden język ``pl``, więc
# ``hide_untranslated``/``redirect_on_fallback`` nie mają tu nic do rozstrzygania. Dlatego
# ``CMS_LANGUAGES`` nie ma w tym pliku.

# --- django CMS 5.1.3 ------------------------------------------------------------------------
# ``CMS_CONFIRM_VERSION4`` nie istnieje już w 5.1.3 (brak w ``cms/utils/conf.py``) – nie ustawiamy.
# Szablony stron = typy stron Wagtaila (tabela 6.1). Szablon jest „typem”: po nim ``apps.pages
# .content`` i ``apps.live.homepage`` rozpoznają aktualności, dokumenty czy stronę partnerów, a pasek
# narzędzi – którą metrykę strony pokazać. Kolejność = kolejność na liście wyboru redaktora.
# ``home``, ``problems``, ``results`` i ``archive_edition`` to szablony stron z danymi żywymi (DJ-01f).
CMS_TEMPLATES = [
    ("dj/pages/content.html", "Strona treści"),
    ("dj/pages/home.html", "Strona główna"),
    ("dj/pages/news_index.html", "Aktualności (lista)"),
    ("dj/pages/news.html", "Aktualność"),
    ("dj/pages/document_index.html", "Dokumenty (spis)"),
    ("dj/pages/document.html", "Dokument"),
    ("dj/pages/partners.html", "Partnerzy"),
    ("dj/pages/faq.html", "Najczęstsze pytania"),
    ("dj/pages/problems.html", "Zadania"),
    ("dj/pages/results.html", "Wyniki"),
    ("dj/pages/archive_index.html", "Archiwum (spis)"),
    ("dj/pages/archive_edition.html", "Edycja w archiwum"),
]


def _slot(name: str, plugins: list[str], limit: int | None = None) -> dict:
    conf: dict = {"name": name, "plugins": plugins}
    if limit is not None:
        conf["limits"] = {"global": limit}
    return conf


# Sloty szablonów i wtyczki dozwolone w każdym z nich (tabela 6.1). Klucze „szablon slot”, bo te
# same nazwy slotów mają w różnych typach różne zestawy (``body`` aktualności to ART, ``body``
# dokumentu – DOC). Zestawy ART/DOC: ``apps/blocks/plugin_sets.py``. Wtyczki-dzieci (pary definicji,
# wiersze harmonogramu, kroki, treść „O Olimpiadzie”) dopuszcza ``child_classes`` rodzica.
# Limit 1 tam, gdzie oryginał ma jedno pole, a nie listę (hasło, sekcje strony głównej, dane żywe).
CMS_PLACEHOLDER_CONF = {
    "dj/pages/home.html hero": _slot("Hasło", ["HeroPlugin"], 1),
    "dj/pages/home.html timeline": _slot("Przebieg zawodów", ["StageTimelinePlugin"], 1),
    "dj/pages/home.html steps": _slot("Jak zacząć", ["StepsSectionPlugin"], 1),
    "dj/pages/home.html about": _slot("O Olimpiadzie", ["AboutSectionPlugin"], 1),
    "dj/pages/news_index.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/news.html body": _slot("Treść", ART_PLUGINS),
    "dj/pages/content.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/content.html attachments": _slot("Pliki do pobrania", ["AttachmentPlugin"]),
    "dj/pages/content.html body": _slot("Treść", DOC_PLUGINS),
    "dj/pages/partners.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/partners.html partners": _slot("Partnerzy", ["PartnerPlugin"]),
    "dj/pages/partners.html become_partner": _slot("Zostań partnerem", ["BecomePartnerPlugin"], 1),
    "dj/pages/problems.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/problems.html problems": _slot("Zadania (z systemu)", ["ProblemsPlugin"], 1),
    "dj/pages/problems.html body": _slot("Treści dodatkowe", ART_PLUGINS),
    "dj/pages/document_index.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/document.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/document.html attachments": _slot("Pliki do pobrania", ["AttachmentPlugin"]),
    "dj/pages/document.html body": _slot("Treść", DOC_PLUGINS),
    "dj/pages/archive_index.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/archive_edition.html summary": _slot("Podsumowanie", TEXT_ONLY),
    "dj/pages/archive_edition.html documents": _slot("Materiały", ["ArchiveDocumentPlugin"]),
    "dj/pages/archive_edition.html results": _slot("Wyniki (z systemu)", ["ArchiveResultsPlugin"], 1),
    "dj/pages/results.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/results.html results": _slot("Wyniki (z systemu)", ["ResultsPlugin"], 1),
    "dj/pages/faq.html intro": _slot("Wprowadzenie", TEXT_ONLY),
    "dj/pages/faq.html faq": _slot("Pytania", ["FAQEntryPlugin"]),
}
# Bufor całych stron **wyłączony**: HTML niesie jednorazowy nonce CSP i dane zawodów z API
# (terminy, stan rejestracji) – strona z bufora miałaby cudzy nonce (skrypty zablokowane) albo
# nieaktualny termin. Bufor placeholderów zostaje, bo wtyczki żywe mają ``cache = False``.
CMS_PAGE_CACHE = False
CMS_PLACEHOLDER_CACHE = True
# Uprawnienia per strona wyłączone – jedna grupa „Redaktorzy” (decyzja z 26.09.2026) na zwykłych
# uprawnieniach modeli Django (``manage.py setup_djcms_groups``).
CMS_PERMISSION = False
# Pasek narzędzi **nie** dla anonimów: jego skrypty wymagają luźnej polityki CSP, a publiczne
# strony mają ścisłą. Redaktor loguje się pod ``/djcms/admin/`` i dopiero wtedy dostaje pasek.
CMS_TOOLBAR_ANONYMOUS_ON = False

# djangocms-versioning 2.7.1: skasowanie wersji (także opublikowanej) wyłączone – to jest cała
# historia treści, odpowiednik rewizji Wagtaila. Wartość domyślna, wpisana jawnie.
DJANGOCMS_VERSIONING_ALLOW_DELETING_VERSIONS = False

# djangocms-text 1.0.1: sanityzacja HTML (``nh3``) przy zapisie – reguła 4 z § 7. Domyślnie
# włączona; wpisana jawnie, bo wyłączenie jej jest dokładnie tym, czego tu nie wolno.
TEXT_HTML_SANITIZE = True
# Edytor (tiptap, domyślny w 1.0.1) z tym samym zestawem formatowania co ``RICH_TEXT_FEATURES``
# Wagtaila: nagłówki h2–h4, pogrubienie, kursywa, listy, linia pozioma, odnośnik, indeksy górny
# i dolny, cytat. Bez obrazów, osadzeń, tabel, kolorów i źródła HTML – obraz i film to osobne
# wtyczki (tak jak osobne bloki w Wagtailu), a redaktor nie pisze HTML-a. ``toolbar_CMS`` to
# pasek wtyczki „Tekst”, ``toolbar_HTMLField`` – pól ``HTMLField`` naszych wtyczek (ramka, hasło,
# odpowiedź FAQ, zaproszenie partnerów).
_TEXT_TOOLBAR = [
    ["Undo", "Redo"],
    ["Paragraph", "Heading2", "Heading3", "Heading4"],
    ["Bold", "Italic", "-", "Subscript", "Superscript"],
    ["Link", "Unlink"],
    ["NumberedList", "BulletedList"],
    ["HorizontalRule", "-", "Blockquote"],
]
TEXT_EDITOR_SETTINGS = {"toolbar_CMS": _TEXT_TOOLBAR, "toolbar_HTMLField": _TEXT_TOOLBAR}
# Wtyczki wewnątrz tekstu (obraz, odnośnik-wtyczka) wyłączone: tekst Wagtaila też ich nie ma.
TEXT_CHILDREN_ENABLED = False
# Bez automatycznego dzielenia wyrazów: djangocms-text wstawiałby do zapisanego HTML-a miękkie
# łączniki (``&shy;``), których tekst po stronie Wagtaila nie ma – łamanie wierszy różniłoby obie
# wersje, a porównanie ma dotyczyć edycji, nie składu.
TEXT_AUTO_HYPHENATE = False
TEXT_PLUGIN_NAME = "Tekst"
TEXT_PLUGIN_MODULE_NAME = "Treść"

# --- Pliki (filer) ---------------------------------------------------------------------------
# Wszystkie adresy aplikacyjne djcms pod jednym prefiksem ``/djcms/`` (DJ-02 D2) – na każdym hoście
# konkursu obok aplikacji głównej, której ``/static/``, ``/media/`` i ``/admin/`` są zajęte.
MEDIA_URL = "/djcms/media/"
MEDIA_ROOT = env("DJCMS_MEDIA_ROOT", default="/app/media")
# Wszystkie pliki filera są publiczne (reguła 12 – ``/djcms/media/*`` serwuje Caddy wprost z wolumenu).
# Uprawnienia per folder wyłączone: redaktorzy są jedną grupą, a pliki i tak są dostępne pod
# jawnym adresem. Flaga „prywatny” w filerze nie jest więc granicą bezpieczeństwa – jest ukryta
# (filer chowa ją przy wyłączonych uprawnieniach) i zablokowana (``apps.blocks.files``); obu
# ustawień pilnuje system check ``dj_blocks.E001``.
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
STATIC_URL = "/djcms/static/"
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
DJCMS_MAIN_API_URL = env("DJCMS_MAIN_API_URL", default="http://web:8000/internal/djcms/v2/")
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

# Rejestr witryn konkursów (DJ-02 D7): co tyle sekund jeden wątek procesu pyta API o listę
# konkursów i uzgadnia rejestr (``apps.sites.registry.refresh_if_due``). ``0`` = bez odświeżania
# leniwego (testy; rejestr uzgadnia wtedy wyłącznie ``manage.py sync_competitions``).
DJCMS_SITES_REFRESH_SECONDS = env.int("DJCMS_SITES_REFRESH_SECONDS", default=60)

# --- Trasy aplikacji głównej i adresy zarezerwowane (S5, S6) -----------------------------------
# ``app_routes.json`` z ``backend/djcms_contract`` (obraz: ``/opt/djcms_contract``). Brak pliku to
# błąd startu: bez niego djcms nie wie, których stron nie wolno publikować (strona pod adresem
# aplikacji byłaby niewidoczna albo przesłaniałaby aplikację). Wyjątek – budowanie obrazu
# (``collectstatic`` nie czyta tras).
DJCMS_CONTRACT_DIR = env("DJCMS_CONTRACT_DIR", default=str(default_contract_dir(BASE_DIR)))
try:
    DJ_APP_ROUTES = load_app_routes(DJCMS_CONTRACT_DIR)
except ContractError as exc:
    if not BUILDING_IMAGE:
        raise ImproperlyConfigured(f"Kontrakt tras aplikacji głównej: {exc}") from None
    DJ_APP_ROUTES = {
        "first_segments": [],
        "nested_paths": [],
        "root_regexes": [],
        "private_prefixes": [],
        "app_re": r"(?!)",
        "app_re_prefixed": r"(?!)",
    }

# Pierwsze segmenty ścieżek, których strona djcms nie może mieć (reguła 5 z § 7 DJ-01, S5 DJ-02):
# własne adresy djcms (``/djcms/…``, ``robots.txt``, ``sitemap.xml``), ścieżki, które Caddy kieruje
# gdzie indziej (``/static/*`` – statyki ``web``), dawne adresy djcms z DJ-01 (``media``, ``filer``)
# i **każdy** pierwszy segment urlconfu aplikacji głównej z kontraktu. Pełna reguła (także drugi
# segment i ``*.html``) – ``apps.pages.validation.path_collides_with_app``.
DJ_OWN_RESERVED_SLUGS = frozenset({"djcms", "robots.txt", "sitemap.xml", "static", "media", "filer"})
DJ_RESERVED_SLUGS = DJ_OWN_RESERVED_SLUGS | frozenset(DJ_APP_ROUTES["first_segments"])

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

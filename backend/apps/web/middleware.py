"""Nagłówek ``Content-Security-Policy`` dla całej aplikacji.

Decyzje (T-08, „Wymagania bezpieczeństwa”):

- ``script-src`` **nie** zawiera ``'unsafe-inline'`` ani ``'unsafe-eval'``. W szablonach nie ma
  ani jednego skryptu inline: HTMX, Alpine (build ``@alpinejs/csp``, który nie ewaluuje wyrażeń)
  i pdf.js przychodzą z dwóch pinowanych CDN-ów, a własny kod żyje w ``static/js/*.js``.
  Do listy dochodzi jednorazowy ``nonce`` – jest przypięty do znaczników ``<script>`` interfejsu,
  więc polityka pozostaje szczelna także wtedy, gdy ktoś kiedyś zawęzi listę hostów,
- ``script-src`` ma też ``'strict-dynamic'``. W przeglądarce, która je rozumie, lista hostów
  i ``'self'`` przestają cokolwiek znaczyć – liczy się wyłącznie nonce oraz zaufanie przekazane
  przez skrypt już zaufany. Hosty CDN zostają jako fallback dla starszych przeglądarek (CSP2
  ignoruje nieznane słowo kluczowe i stosuje listę hostów), a każdy nasz ``<script>`` ma nonce,
  więc dla nowych przeglądarek to zacieśnienie, nie rozluźnienie.

  Dlaczego dynamiczny ``import()`` pdf.js dalej działa: ``static/js/review-annotations.js`` jest
  ładowany jako ``<script type="module" nonce=…>``, więc sam jest zaufany. Żądanie modułu
  wystawione przez ``import()`` ma w CSP3 metadanę „nie wstawione przez parser”, a dla takich
  żądań ``'strict-dynamic'`` przepuszcza pobranie niezależnie od hosta (CSP3 §6.6.1, „script
  directives pre-request check”). Innymi słowy: zaufanie propaguje się z modułu na jego importy,
  dokładnie tak samo jak na ``document.createElement("script")``.
- ``style-src`` ma ``'unsafe-inline'`` **świadomie**: HTMX ustawia style przejść na elementach
  (``htmx-indicator``), a warstwa adnotacji pdf.js pozycjonuje prostokąty przez ``style.left/top``.
  Styl inline nie wykonuje kodu, więc ryzyko jest nieporównywalnie mniejsze niż przy skryptach;
  usunięcie tego wyjątku wymagałoby rezygnacji z HTMX albo własnego builda z nonce na każdym stylu.
  Do listy hostów dochodzi ``cdn.jsdelivr.net`` – arkusz Swagger UI na ``/api/docs/``. Przy już
  obecnym ``'unsafe-inline'`` dopisanie hosta niczego nie osłabia,
- ``connect-src``, ``img-src`` i ``media-src`` zawierają dodatkowo publiczny host MinIO
  (``S3_PUBLIC_ENDPOINT_URL``). ``connect-src``, bo pdf.js pobiera plik rozwiązania przez ``fetch``,
  a endpoint pobrania przekierowuje na presigned URL. ``img-src`` i ``media-src``, bo od T-09
  obrazy i renditions Wagtaila stoją w publicznym buckecie i są linkowane bezpośrednio spod hosta
  MinIO – bez tego wpisu przeglądarka blokuje **każdą** ilustrację redakcyjną w produkcji
  (w devie storage jest lokalny i wpada w ``'self'``, więc problem nie byłby widoczny),
- ``frame-src`` to zamknięta lista dostawców osadzeń (``EmbedBlock``): YouTube i Vimeo, dokładnie
  te same, na które zawężony jest ``WAGTAILEMBEDS_FINDERS``. Nagłówek i finder muszą wymieniać
  te same hosty – finder decyduje, co redaktor może wstawić, CSP, co przeglądarka wykona,
- ``object-src 'none'``, ``base-uri 'self'``, ``frame-ancestors 'none'`` – standardowa domknięta baza,
- hosty **Google Analytics 4** dochodzą do ``script-src``, ``connect-src`` i ``img-src``
  **warunkowo**: tylko wtedy, gdy organizator wpisał identyfikator ``G-…`` w ``/cms/``
  (``cms.SiteSettings.ga_measurement_id``). Serwis bez identyfikatora ma nagłówek co do bajtu
  taki, jak przed dodaniem analityki – polityka nie wymienia wtedy ani jednego hosta Google'a,
  bo pozwolenie na coś, czego strona nigdy nie wczyta, jest tylko poszerzeniem powierzchni ataku.
  Sam odczyt ustawienia nie może kosztować zapytania na żądanie (nagłówek powstaje także dla
  plików statycznych) – patrz ``apps/cms/analytics.py``.

Nagłówek jest ustawiany na **każdej** odpowiedzi. Dla panelu redakcyjnego (Wagtail) i panelu
Django obowiązuje jednak **osobna, luźniejsza** polityka:

- oba panele wstrzykują skrypty i style inline (Wagtail dodatkowo używa telepathu i Draftaila,
  panel Django – widgetów kalendarza), więc polityka nonce-only wyłączyłaby je w całości.
  Przepisanie ich szablonów nie jest w naszej gestii: to kod bibliotek,
- ryzyko jest ograniczone zakresem: obie ścieżki wymagają zalogowania i uprawnień
  (``access_admin`` / ``is_staff``), a treści od anonimów nigdy się w nich nie renderują,
- **strony publiczne pozostają bez ``'unsafe-inline'`` dla skryptów** – to jest testowane
  (``apps/web/tests/test_public.py`` oraz ``apps/cms/tests/test_security.py``).

Uwaga implementacyjna: w polityce panelu **nie ma** nonce'a. Przeglądarka, widząc ``nonce-…``
w ``script-src``, ignoruje ``'unsafe-inline'`` – doklejenie obu naraz dałoby politykę pozornie
luźną i faktycznie blokującą panel.

Jak rozpoznajemy panel: patrz ``is_admin_request``. Prefiks ścieżki jest ostatnim, a nie
pierwszym kryterium, i nie jest zapisany literałem – bierze się z ``reverse()``, więc przeniesienie
panelu w ``config/urls.py`` nie zostawia po sobie polityki dopasowanej do starego adresu.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from django.conf import settings
from django.urls import NoReverseMatch, reverse

#: CDN-y, z których wolno ładować skrypty. Pinowanie wersji i SRI są w szablonie ``base.html``.
SCRIPT_CDN_SOURCES = ("https://cdnjs.cloudflare.com", "https://cdn.jsdelivr.net")

#: CDN-y arkuszy stylów. Wyłącznie Swagger UI na ``/api/docs/`` – reszta serwisu ma własne CSS.
STYLE_CDN_SOURCES = ("https://cdn.jsdelivr.net",)

NONCE_BYTES = 16

#: Hosty ramek dla ``EmbedBlock``. Muszą odpowiadać ``WAGTAILEMBEDS_FINDERS`` z ``settings/base.py``:
#: oEmbed YouTube'a zwraca ``<iframe src="https://www.youtube.com/embed/…">`` (albo wariant
#: ``youtube-nocookie`` przy ``?rel=0``), Vimeo – ``https://player.vimeo.com/video/…``.
EMBED_FRAME_SOURCES = (
    "https://www.youtube.com",
    "https://www.youtube-nocookie.com",
    "https://player.vimeo.com",
)

#: Hosty Google Analytics 4. Dochodzą do polityki **wyłącznie** wtedy, gdy w ``cms.SiteSettings``
#: stoi identyfikator ``G-…`` (patrz ``apps/cms/analytics.py``). Bez identyfikatora nagłówek jest
#: co do bajtu taki, jak przed dodaniem analityki – i to jest testowane.
#:
#: Skąd te trzy listy:
#:
#: - ``script-src``: ``gtag/js`` wczytuje ``static/js/analytics.js``. Nowa przeglądarka przepuści
#:   go i bez tego wpisu (skrypt wstrzykujący ma nonce, a ``'strict-dynamic'`` przenosi zaufanie
#:   na ``createElement("script")``), host jest więc fallbackiem dla CSP2 – dokładnie tak samo,
#:   jak oba CDN-y wyżej,
#: - ``connect-src``: zdarzenia idą POST-em/``sendBeacon`` na ``*.google-analytics.com``, a przy
#:   pomiarze po stronie serwera Google'a także na ``*.analytics.google.com``; sam ``gtag/js``
#:   dociąga z ``googletagmanager.com`` konfigurację strumienia,
#: - ``img-src``: starszy wariant wysyłki to ``collect`` jako obrazek 1×1 (fallback, gdy
#:   ``sendBeacon`` jest niedostępny) – bez tego wpisu część odsłon po prostu nie doszłaby.
#:
#: Czego tu **nie ma** i nie ma być: hostów reklamowych (``doubleclick.net``,
#: ``googleadservices.com``). Loader wyłącza Google Signals i personalizację reklam, więc gdyby
#: kiedyś zaczęły być wołane, znaczyłoby to, że ustawienie usługi rozjechało się z polityką –
#: i lepiej, żeby przeglądarka je wtedy zablokowała.
ANALYTICS_SCRIPT_SOURCES = ("https://www.googletagmanager.com",)
ANALYTICS_CONNECT_SOURCES = (
    "https://*.google-analytics.com",
    "https://*.analytics.google.com",
    "https://www.googletagmanager.com",
)
ANALYTICS_IMG_SOURCES = ("https://*.google-analytics.com", "https://www.googletagmanager.com")

#: Ekrany zgody dostawców OAuth. Trafiają do ``form-action`` **tylko** dla dostawcy, który ma
#: skonfigurowane klucze (patrz ``provider_form_action_sources``).
#:
#: Dlaczego to jest potrzebne, skoro allauth nie wysyła POST-a do dostawcy: przycisk „Kontynuuj
#: z Google” jest POST-em na nasz adres (``/accounts/google/login/``, CSRF), a ten odpowiada
#: przekierowaniem 302 na ekran zgody dostawcy. Przeglądarki nie są zgodne co do tego, czy
#: ``form-action`` obowiązuje także dla przekierowań będących skutkiem wysłania formularza –
#: Chrome i Safari historycznie sprawdzały cel przekierowania i blokowały je przy ``'self'``.
#: Rozluźnienie jest wąskie i warunkowe: dwa konkretne originy, wyłącznie gdy dostawca jest włączony.
PROVIDER_FORM_ACTION_SOURCES = {
    "google": "https://accounts.google.com",
    "facebook": "https://www.facebook.com",
}

#: Nazwy widoków/przestrzeni nazw panelu. ``admin`` to panel Django (ma własną przestrzeń nazw),
#: ``wagtailadmin_`` to prefiks nazw widoków Wagtaila – ten montuje się **bez** przestrzeni nazw,
#: więc ``resolver_match.namespace`` dla ``/cms/`` jest pustym stringiem.
ADMIN_URL_NAMESPACES = frozenset({"admin", "wagtailadmin"})
ADMIN_VIEW_NAME_PREFIX = "wagtailadmin_"

#: Nazwy adresów, spod których wyprowadzamy prefiksy panelu. Fallback (gdy panel nie jest
#: zamontowany) trzyma historyczne literały – żeby middleware nie wywracał się na testowym urlconfie.
ADMIN_ROOT_URL_NAMES = ("wagtailadmin_home", "admin:index")
FALLBACK_ADMIN_PATH_PREFIXES = ("/cms/", "/admin/")


def _origin(url: str | None) -> str:
    """Sam schemat i host z URL-a z ustawień. Pusty string, gdy nic sensownego nie podano."""
    parts = urlsplit((url or "").strip())
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}"
    return ""


def storage_origin() -> str:
    """Origin publicznego bucketu mediów (obrazy i dokumenty Wagtaila). Pusty w devie."""
    return _origin(getattr(settings, "S3_PUBLIC_ENDPOINT_URL", ""))


def _with_storage(sources: list[str]) -> str:
    """Lista źródeł powiększona o origin publicznego storage, gdy taki jest skonfigurowany."""
    origin = storage_origin()
    return " ".join([*sources, origin] if origin else sources)


def provider_form_action_sources() -> tuple[str, ...]:
    """Originy ekranów zgody **włączonych** dostawców OAuth (puste, gdy żaden nie ma kluczy).

    Źródłem prawdy jest ta sama konfiguracja, którą czyta allauth: obecność klucza ``APPS``
    w ``SOCIALACCOUNT_PROVIDERS``. Instalacja bez OAuth ma dokładnie taką politykę, jak przed
    dodaniem logowania społecznościowego.
    """
    providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {}) or {}
    return tuple(
        origin
        for name, origin in PROVIDER_FORM_ACTION_SOURCES.items()
        if (providers.get(name) or {}).get("APPS")
    )


def form_action_sources() -> str:
    """Wartość dyrektywy ``form-action``: zawsze ``'self'``, plus ekrany zgody dostawców OAuth."""
    return " ".join(["'self'", *provider_form_action_sources()])


def build_policy(nonce: str, *, analytics: bool = False) -> str:
    """Buduje treść polityki dla jednego żądania (nonce jest jednorazowy).

    ``analytics`` dokłada hosty Google Analytics 4 – i tylko wtedy, gdy organizator wpisał
    identyfikator w ``/cms/``. Domyślne ``False`` jest tu świadome: funkcja wołana bez tego
    argumentu (testy, ewentualny inny kod) zwraca politykę sprzed dodania analityki.
    """
    # Kolejność jest istotna dla starych przeglądarek: nonce i hosty muszą stać przed
    # 'strict-dynamic', bo CSP2 po prostu pominie nieznane słowo kluczowe i użyje reszty listy.
    analytics_scripts = ANALYTICS_SCRIPT_SOURCES if analytics else ()
    script_src = ["'self'", f"'nonce-{nonce}'", *SCRIPT_CDN_SOURCES, *analytics_scripts, "'strict-dynamic'"]
    media_sources = _with_storage(["'self'", "data:", "blob:"])
    # ``img-src`` i ``media-src`` różnią się wyłącznie hostami analityki: piksel pomiarowy jest
    # obrazkiem, ale żaden plik audio ani wideo nie przychodzi z Google'a.
    img_sources = _with_storage(["'self'", "data:", "blob:", *(ANALYTICS_IMG_SOURCES if analytics else ())])
    connect_sources = _with_storage(
        ["'self'", *SCRIPT_CDN_SOURCES, *(ANALYTICS_CONNECT_SOURCES if analytics else ())]
    )
    directives = [
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        f"form-action {form_action_sources()}",
        # Obrazy i pliki mediów redakcyjnych stoją w publicznym buckecie MinIO i są linkowane
        # bezpośrednio (URL bez podpisu) – jego origin musi być na liście, inaczej produkcja
        # blokuje każdą ilustrację i każdy rendition Wagtaila.
        f"img-src {img_sources}",
        f"media-src {media_sources}",
        "font-src 'self' data:",
        # Zobacz docstring modułu: wyjątek dotyczy wyłącznie stylów, nigdy skryptów.
        f"style-src 'self' 'unsafe-inline' {' '.join(STYLE_CDN_SOURCES)}",
        f"script-src {' '.join(script_src)}",
        f"connect-src {connect_sources}",
        # Zamknięta lista dostawców osadzeń – ta sama, na którą zawężony jest WAGTAILEMBEDS_FINDERS.
        f"frame-src {' '.join(EMBED_FRAME_SOURCES)}",
        # pdf.js uruchamia worker; przy CDN cross-origin robi to przez blob: (fallback biblioteki).
        "worker-src 'self' blob:",
    ]
    return "; ".join(directives)


def build_admin_policy() -> str:
    """Polityka panelu (``/cms/``, ``/admin/``). Świadomie z ``'unsafe-inline'`` dla skryptów.

    ``frame-ancestors 'self'``, a nie ``'none'``: podgląd strony w Wagtailu osadza własny adres
    w ``<iframe>`` tej samej domeny. Ramek z obcych domen nadal nie ma.
    """
    media_sources = _with_storage(["'self'", "data:", "blob:"])
    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'self'",
            "form-action 'self'",
            # Podgląd obrazu w bibliotece mediów i miniatura w wyborze obrazu idą prosto
            # z publicznego bucketu – bez originu redaktor widzi w /cms/ same połamane ikony.
            f"img-src {media_sources}",
            "font-src 'self' data:",
            f"media-src {media_sources}",
            "style-src 'self' 'unsafe-inline'",
            # Patrz docstring modułu: wyjątek dotyczy wyłącznie ścieżek panelu.
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
            "connect-src 'self'",
            "worker-src 'self' blob:",
            # Osadzenia (EmbedBlock) w podglądzie: bez tego edytor nie pokaże wstawionego filmu.
            "frame-src 'self' https:",
        ]
    )


def admin_path_prefixes() -> tuple[str, ...]:
    """Prefiksy panelu wyprowadzone z ``config/urls.py`` przez ``reverse()``.

    Literał ``/cms/`` w kodzie middleware oznaczałby, że przeniesienie panelu w urlconfie po cichu
    zostawia luźną politykę pod starym adresem, a panel pod nowym – bez działających skryptów.
    """
    prefixes = []
    for name in ADMIN_ROOT_URL_NAMES:
        try:
            prefixes.append(reverse(name))
        except NoReverseMatch:  # pragma: no cover - panel zawsze zamontowany w config/urls.py
            continue
    return tuple(prefixes) or FALLBACK_ADMIN_PATH_PREFIXES


def is_admin_path(path: str) -> bool:
    """Czy ścieżka należy do panelu. Porównanie po prefiksie, na znormalizowanej ścieżce."""
    return any(path.startswith(prefix) for prefix in admin_path_prefixes())


def is_admin_request(request) -> bool:
    """Czy żądanie trafiło do panelu redakcyjnego/administracyjnego.

    Kolejność kryteriów jest celowa:

    1. **przestrzeń nazw** z ``resolver_match`` – panel Django ma ``admin``. Ten sygnał pochodzi
       z urlconfa, nie z tekstu adresu, więc nie da się go podrobić ścieżką,
    2. **nazwa widoku** ``wagtailadmin_…`` – rdzeń panelu Wagtaila montuje się **bez** przestrzeni
       nazw (``resolver_match.namespace`` jest tam pustym stringiem), więc punkt 1 by go nie złapał,
    3. **prefiks ścieżki** (z ``reverse()``) – reszta panelu Wagtaila ma własne przestrzenie nazw
       (``wagtailimages``, ``wagtaildocs``, ``wagtailsnippets``…), a wypisywanie ich listy
       rozjeżdżałoby się z każdą wersją biblioteki. Ten sam warunek obsługuje odpowiedzi bez
       ``resolver_match``: 404 pod ``/cms/nie-ma/`` albo wyjątek złapany przed rozwiązaniem adresu.
    """
    match = getattr(request, "resolver_match", None)
    if match is not None:
        namespaces = set(match.namespaces or ())
        if match.namespace:
            namespaces.add(match.namespace)
        if namespaces & ADMIN_URL_NAMESPACES:
            return True
        if (match.view_name or "").rsplit(":", 1)[-1].startswith(ADMIN_VIEW_NAME_PREFIX):
            return True
    return is_admin_path(request.path)


class ContentSecurityPolicyMiddleware:
    """Nadaje żądaniu ``csp_nonce`` i dokleja nagłówek CSP do odpowiedzi."""

    header = "Content-Security-Policy"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        nonce = secrets.token_urlsafe(NONCE_BYTES)
        # Nonce musi istnieć zanim szablon zacznie się renderować – stąd przypisanie przed widokiem.
        request.csp_nonce = nonce
        response = self.get_response(request)
        if self.header not in response:
            if is_admin_request(request):
                response[self.header] = build_admin_policy()
            else:
                # Import w środku: ``apps.cms`` ładuje modele Wagtaila, a middleware powstaje
                # przy starcie procesu. Odczyt jest pamiętany w module (TTL), więc nie ma tu
                # zapytania do bazy na każdy plik statyczny – patrz apps/cms/analytics.py.
                from apps.cms.analytics import analytics_enabled

                response[self.header] = build_policy(nonce, analytics=analytics_enabled())
        return response

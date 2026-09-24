"""Pełnostronicowy cache odpowiedzi HTML dla anonimowych GET-ów stron publicznych.

**Dlaczego własna warstwa, a nie wbudowana para**
``django.middleware.cache.UpdateCacheMiddleware``/``FetchFromCacheMiddleware``:
wbudowana para liczy klucz z ``Vary`` i ciasteczek żądania (``django.utils.cache.get_cache_key``),
więc przy sesji dociągniętej przez ``SessionMiddleware`` (a każde żądanie ją dociąga – patrz
``apps.accounts.preferences``) klucz zależałby od identyfikatora sesji **gościa**, czyli w praktyce
nigdy by nie trafiał. Gorzej: standardowa para nie wie nic o nonce'ie CSP (``apps.web.middleware``)
ani o tokenie CSRF wstrzykiwanym do **każdej** strony (``templates/base.html``, atrybut
``hx-headers`` w znaczniku ``<body>``) – zapisałaby oba w cache'u i odtwarzała je nieświeże, czyli
odpowiedź z zablokowanymi skryptami (zły nonce) i/albo z tokenem, który nie pasuje do niczyjego
ciasteczka. Własna warstwa zna oba te fakty i placeholderuje je świadomie (patrz niżej).

**Zasięg (allow-list, nie deny-list):** wyłącznie adresy z ``ALLOWED_PATHS``/``ALLOWED_PREFIXES`` –
strony części informacyjnej (``apps.cms``) i ``/statystyki/``. Nigdy: panel koordynatora, konto,
API, formularze rejestracji, CMS/admin – każdy z nich renderuje coś zależnego od tożsamości albo
przyjmuje POST, więc nie ma go nawet sensu wymieniać w deny-liście.

**Miejsce w ``MIDDLEWARE`` (``config/settings/base.py``) jest częścią kontraktu.** Warstwa stoi
**bezpośrednio przed** ``wagtail.contrib.redirects.middleware.RedirectMiddleware``, czyli jest
**ostatnia przed widokiem** – i to jest zamierzone z dwóch powodów:

1. **Trafienie ma być tanie.** Skoro stoi na samym końcu łańcucha, każda warstwa nad nią
   (``SecurityMiddleware``, CSP, licznik 5xx, sesja, język, CSRF, uwierzytelnienie, konkurs,
   preferencje, 2FA, komunikaty, ``X-Frame-Options``, allauth) i tak wykonuje swoją część **zawsze**
   – również przy trafieniu, bo trafienie tylko pomija to, co jest **pod** tą warstwą: rozstrzyganie
   adresu i widok. Dzięki temu nagłówki bezpieczeństwa, ciasteczko sesji (tylko gdy zmienione) i
   ciasteczko CSRF (patrz niżej) powstają **dokładnie tak samo** przy trafieniu i przy chybieniu –
   nie trzeba ich tu duplikować ani ryzykować rozjazdu z tym, co robią biblioteki wyżej.
2. **Token CSRF da się bezpiecznie odświeżyć.** ``CsrfViewMiddleware`` stoi wyżej w łańcuchu (a
   więc *owija* tę warstwę) i dokłada ciasteczko ``csrftoken`` w swojej fazie odpowiedzi – czyli
   **po** tym, jak ta warstwa już zwróciła swoją odpowiedź. Wystarczy więc wywołać
   ``django.middleware.csrf.get_token(request)`` (ta sama funkcja, na której stoi
   ``@ensure_csrf_cookie``) tuż przed oddaniem trafienia: ustawia ona
   ``request.META["CSRF_COOKIE_NEEDS_UPDATE"]``, a ``CsrfViewMiddleware`` samo dogląda ciasteczka –
   bez odgadywania ani kopiowania jego wewnętrznej logiki maskowania sekretu.

**Nonce CSP.** ``apps.web.middleware.ContentSecurityPolicyMiddleware`` przypisuje
``request.csp_nonce`` na **samym początku** łańcucha, więc w chwili, gdy ta warstwa działa, nonce
bieżącego żądania już istnieje – również przy trafieniu. Przy zapisie do cache'u zamieniamy
**każde** wystąpienie ówczesnego nonce'u (w treści i w zapisanym nagłówku CSP) na placeholder,
a przy odtwarzaniu wstawiamy w jego miejsce nonce **tego** żądania. ``ContentSecurityPolicyMiddleware``
nie nadpisze naszego nagłówka (``if self.header not in response``), więc odtworzony CSP zostaje.

**Token CSRF w treści.** ``templates/base.html`` wstawia ``{{ csrf_token }}`` w atrybucie
``hx-headers`` znacznika ``<body>`` – **na każdej** stronie, nie tylko tam, gdzie jest formularz
(stąd HTMX potrafi wysłać POST z dowolnego miejsca serwisu). Token jest maskowany na nowo przy
każdym wywołaniu ``get_token()``, więc nie da się go „odtworzyć” z niczego zapamiętanego – zamiast
tego szukamy **dokładnie tego stringa**, który trafił do wyrenderowanej treści (wzorzec
``_CSRF_TOKEN_RE``, jedyne miejsce, w którym token naprawdę stoi), zamieniamy go na placeholder przy
zapisie i na świeży ``get_token(request)`` przy odtworzeniu. Więcej niż jedno dopasowanie w treści –
nie wiadomo, które podmienić – więc taka odpowiedź **w ogóle nie trafia do cache'u** (patrz
``_placeholder_body``).

**Nigdy nie cache'ujemy:**

- żądań innych niż ``GET``/``HEAD`` i żądań zalogowanych (``request.user.is_authenticated``) –
  odpowiedź zależy od tożsamości albo modyfikuje stan,
- adresów spoza allow-listy i adresów z parametrami zapytania innymi niż wyłącznie ``?page=<liczba>``
  – nieznany parametr może zmieniać treść w sposób, którego klucz nie widzi,
- odpowiedzi z kodem innym niż 200, z ``Content-Type`` innym niż ``text/html`` i z nagłówkiem
  ``Vary`` (ktoś już zadeklarował, że treść zależy od czegoś, czego nasz klucz nie obejmuje),
- odpowiedzi, które same ustawiają ciasteczko (``response.cookies`` niepusty **na wyniku widoku**,
  zanim jeszcze doszły do niego ciasteczka sesji/CSRF dokładane wyżej w łańcuchu – patrz punkt
  o miejscu w ``MIDDLEWARE``) – to sygnał, że widok robi coś specyficznego dla tego gościa,
- żądań, w których sesja **zmieniła się** w trakcie obsługi (``request.session.modified``) – dla
  przełącznika wysokiego kontrastu gościa (``apps.accounts.preferences.CONTRAST_SESSION_KEY``) –
  to jest stan **tej przeglądarki**, a nie treść wspólna dla wszystkich anonimowych gości,
- żądań, w których **komunikat organizatora został wyświetlony** (``django.contrib.messages``,
  ``MESSAGE_STORAGE`` = sesja). Uwaga implementacyjna: ``request.session.modified`` **nie** jest tu
  wystarczające – ``MessageMiddleware`` konsumuje kolejkę i zapisuje sesję dopiero w swojej fazie
  odpowiedzi, a ta warstwa stoi **niżej** w łańcuchu (patrz punkt o miejscu w ``MIDDLEWARE``), więc
  widzi sesję **przed** tym zapisem. Sygnałem, który jest widoczny na czas, jest sam magazyn
  komunikatów (``request._messages`` – ustawia go ``MessageMiddleware`` w fazie żądania):
  ``{% if messages %}`` w szablonie odczytuje go **w trakcie renderowania**, czyli zanim ta warstwa
  w ogóle dostanie odpowiedź z powrotem, i ustawia ``storage.used``/``storage.added_new`` od razu,
  bez czekania na zapis do sesji,
- żądań gościa, który **już** ma w sesji ustawiony wysoki kontrast (``CONTRAST_SESSION_KEY``) –
  strona renderuje się dla niego inaczej (``data-contrast="high"`` na ``<html>``), a to jest tak
  rzadkie (gość musiał już raz kliknąć przełącznik), że nie opłaca się poszerzać nim klucza,
- odpowiedzi dłuższych niż ``PAGE_CACHE_MAX_BYTES`` – zabezpieczenie przed jedną olbrzymią stroną
  wypychającą z Redisa wpisy wszystkich pozostałych.

**Klucz:** ``(wersja globalna, wersja witryny konkursu, konkurs, język interfejsu, ścieżka,
parametr page)`` – patrz ``build_key``. Wersje to liczniki w Redisie: unieważnienie = ``INCR``,
nigdy enumeracja istniejących wpisów. Wersja **witryny** obejmuje zdarzenia przypisane do
konkretnego konkursu (publikacja/wycofanie/przeniesienie/skasowanie strony, zapis ``SiteSettings``,
komunikat organizatora przypisany do konkursu, zmiana edycji/etapu/wydarzenia/publikacji wyników,
zapis i skasowanie plakatu do pobrania).
Wersja **globalna** obejmuje to, czego nie da się przypisać do jednej witryny (komunikat bez
konkursu – patrz ``apps.cms.models.Announcement.competition``, pole nullowalne) – bumpuje wtedy
klucze **wszystkich** witryn naraz, bez ich wyliczania.

**Nagłówki:** ``Content-Type``, ``Content-Language`` i CSP wracają takie, jakie były (CSP ze świeżym
nonce'em). ``X-Page-Cache: HIT|MISS|BYPASS`` – wyłącznie do weryfikacji, koszt jednego przypisania.
``Cache-Control: private, no-store`` – dokładany **wprost** (``setdefault``, więc widok, który sam
ustawi ten nagłówek, wygrywa) na każdej odpowiedzi HIT i MISS z tej warstwy: cache jest wyłącznie
po stronie serwera, a bez tego jawnego zaprzeczenia domyślne zachowanie proxy/CDN-u przed Caddym
(dziś go nie ma, ale to nie jest gwarancja na zawsze) mogłoby kiedyś zacząć buforować odpowiedź
z materializowanym nonce'em i tokenem CSRF – czyli dokładnie to, czemu ta warstwa ma zapobiegać.

**Awaria Redisa nie ma prawa wywrócić strony.** ``CACHES["default"]["OPTIONS"]["IGNORE_EXCEPTIONS"]``
(``config/settings/base.py``) każe ``django-redis`` połykać błędy połączenia i zwracać ``None``/nic
nie robić; ta warstwa dodatkowo owija własne wywołania (``_safe_get``/``_safe_set``/``_safe_incr``)
w drugie, niezależne zabezpieczenie – żądanie ma się wyrenderować normalnie (BYPASS albo MISS bez
zapisu), a nie skończyć pięćsetką, gdy Redis akurat nie odpowiada.
"""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.middleware.csrf import get_token

logger = logging.getLogger(__name__)

# --- Zasięg: dozwolone adresy (allow-list) -------------------------------------------------------

#: Adresy dopasowywane dokładnie. ``request.path_info`` – czyli już **bez** prefiksu konkursu
#: w trybie ścieżkowym (``apps.tenancy.middleware.CompetitionMiddleware`` zdejmuje go, zanim
#: urlconf zobaczy żądanie) – izolację między konkursami i tak niesie klucz (patrz ``build_key``).
ALLOWED_PATHS = frozenset(
    {
        "/",
        "/harmonogram/",
        "/warsztaty/",
        "/faq/",
        "/partnerzy/",
        "/kontakt/",
        "/wyniki/",
        "/statystyki/",
        # Lista plakatów do pobrania (``apps.web.views.posters``). Wyłącznie **lista** – adres
        # pobrania (``/plakaty/<id>/pobierz/``) nie pasuje do żadnego wpisu tej listy ani do
        # prefiksu niżej, więc każde pobranie dochodzi do widoku i do licznika.
        "/plakaty/",
    }
)

#: Sekcje z potomkami w drzewie stron: dokumenty, aktualności, archiwum edycji. Prefiks, nie
#: dokładny adres – liczba i slugi stron pod nimi należą do redakcji, nie do tej listy.
ALLOWED_PREFIXES = ("/dokumenty/", "/aktualnosci/", "/archiwum/")


def is_cacheable_path(path_info: str) -> bool:
    """Czy ten adres w ogóle wolno cache'ować – niezależnie od reszty żądania."""
    if path_info in ALLOWED_PATHS:
        return True
    return any(path_info.startswith(prefix) for prefix in ALLOWED_PREFIXES)


# --- Klucz i wersje -------------------------------------------------------------------------------

CACHE_PREFIX = "web:page_cache"
VERSION_PREFIX = f"{CACHE_PREFIX}:v"
METRIC_PREFIX = f"{CACHE_PREFIX}:metric"

#: Domyślny czas życia wpisu (sekundy). Nadpisywalny ``PAGE_CACHE_SECONDS`` w ustawieniach; zero
#: wyłącza cache tak samo jak ``PAGE_CACHE_ENABLED = False`` (patrz ``config/settings/base.py``).
DEFAULT_TTL_SECONDS = 120

#: Odpowiedzi dłuższe niż to nie trafiają do cache'a (patrz ``_storable``) – zabezpieczenie przed
#: jedną nietypowo dużą stroną wypychającą z Redisa wpisy wszystkich pozostałych. 512 KiB jest
#: kilkanaście razy więcej niż największa strona z allow-listy waży dziś w praktyce.
PAGE_CACHE_MAX_BYTES = 512 * 1024

#: Nagłówek, który ta warstwa dokłada każdej odpowiedzi HIT/MISS, gdy widok sam żadnego nie ustawił
#: (patrz docstring modułu, sekcja „Nagłówki”).
CACHE_CONTROL_VALUE = "private, no-store"


def _ttl_seconds() -> int:
    return int(getattr(settings, "PAGE_CACHE_SECONDS", DEFAULT_TTL_SECONDS))


def feature_enabled() -> bool:
    """Czy warstwa ma w ogóle uczestniczyć w tym procesie – dwa niezależne wyłączniki naraz."""
    return bool(getattr(settings, "PAGE_CACHE_ENABLED", False)) and _ttl_seconds() > 0


def _competition_id(request) -> int | None:
    competition = getattr(request, "competition", None)
    return getattr(competition, "pk", None)


# --- Bezpieczne wywołania cache'a: awaria Redisa nie ma prawa wywrócić żądania --------------------
#
# ``IGNORE_EXCEPTIONS`` w ``CACHES["default"]`` (config/settings/base.py) już każe ``django-redis``
# połykać błędy połączenia, ale to ustawienie backendu – druga, niezależna warstwa tutaj nie zależy
# od tego, czy ktoś go kiedyś wyłączy albo podmieni backend na taki, który tego nie robi.


def _safe_get(key: str, default=None):
    try:
        return cache.get(key, default)
    except Exception:  # noqa: BLE001 - patrz nagłówek sekcji: awaria cache'a nie ma tu prawa głosu
        logger.warning("Cache stron publicznych: błąd backendu (get) – żądanie bez cache'a.", exc_info=True)
        return default


def _safe_set(key: str, value, timeout) -> None:
    try:
        cache.set(key, value, timeout)
    except Exception:  # noqa: BLE001
        logger.warning("Cache stron publicznych: błąd backendu (set) – wpis pominięty.", exc_info=True)


def _safe_incr(key: str) -> bool:
    """``True``, gdy podbicie się udało – albo backend akurat milczy z powodu awarii.

    Nie da się tych dwóch przypadków odróżnić bez drugiego zapytania, a to nie jest koszt, który
    warto tu płacić: skutek błędnego ``True`` jest wyłącznie taki, że nie próbujemy dodatkowo
    ``cache.set`` – a to wywołanie i tak by padło z tego samego powodu.
    """
    try:
        cache.incr(key)
        return True
    except ValueError:
        return False
    except Exception:  # noqa: BLE001
        logger.warning("Cache stron publicznych: błąd backendu (incr) – wersja nie podbita.", exc_info=True)
        return True


def _version(key: str) -> int:
    """Wersja spod klucza – ``1``, gdy nikt jeszcze nie unieważniał (klucz nie istnieje)."""
    value = _safe_get(key)
    return int(value) if value is not None else 1


def _versions(competition_id: int | None) -> tuple[int, int]:
    global_version = _version(f"{VERSION_PREFIX}:global")
    site_version = _version(f"{VERSION_PREFIX}:{competition_id}") if competition_id is not None else 1
    return global_version, site_version


def _bump(key: str) -> None:
    """``INCR`` na wersji – klucz bez wartości startuje od ``1``, więc bump daje od razu ``2``."""
    if not _safe_incr(key):
        _safe_set(key, 2, None)


def invalidate_competition(competition_id: int | None) -> None:
    """Unieważnia wpisy **jednej** witryny konkursu. ``None`` = nie wiadomo której → wszystkie."""
    if competition_id is None:
        invalidate_all()
        return
    _bump(f"{VERSION_PREFIX}:{competition_id}")


def invalidate_all() -> None:
    """Unieważnia wpisy **wszystkich** witryn naraz – bez ich wyliczania (patrz docstring modułu)."""
    _bump(f"{VERSION_PREFIX}:global")


def _query_suffix(request) -> str | None:
    """Sufiks klucza z parametrów zapytania, albo ``None``, gdy zapytanie nie jest obsługiwane.

    Pusty string dla żądania bez ``?...`` w ogóle. Wolno wyłącznie ``?page=<liczba>`` – jedyny
    parametr, o którym wiadomo, że **jest** obsłużony przez strony na allow-liście (paginacja
    newsroomu/archiwum) i że jego wartość jednoznacznie opisuje, co się renderuje.
    """
    query = request.META.get("QUERY_STRING", "")
    if not query:
        return ""
    if "&" in query or not query.startswith("page="):
        return None
    value = query[len("page=") :]
    return f"page={value}" if value.isdigit() else None


def build_key(request) -> str | None:
    """Klucz cache'a dla tego żądania, albo ``None``, gdy zapytanie nie kwalifikuje się do klucza.

    ``GET`` i ``HEAD`` dostają **ten sam** klucz – to celowe, nie przeoczenie. Widok, który nie
    definiuje własnego ``head()`` (żaden z allow-listy tego nie robi), dostaje go od Django jako
    alias ``get()`` (``django.views.generic.View.head = View.get``), więc obie metody i tak
    renderują identyczną treść; osobny klucz podwoiłby liczbę wpisów bez żadnej korzyści.
    """
    query = _query_suffix(request)
    if query is None:
        return None
    competition_id = _competition_id(request)
    global_version, site_version = _versions(competition_id)
    language = getattr(request, "LANGUAGE_CODE", settings.LANGUAGE_CODE)
    return ":".join(
        [
            CACHE_PREFIX,
            str(global_version),
            str(site_version),
            str(competition_id or "none"),
            language,
            request.path_info,
            query,
        ]
    )


# --- Kwalifikacja żądania -------------------------------------------------------------------------


def _high_contrast_set(request) -> bool:
    """Czy gość ma w sesji zapisany wysoki kontrast – jedyny wymiar sesji, o który tu pytamy.

    Import lokalny: ``apps.accounts.preferences`` to też warstwa pośrednia, a obie ładują się przy
    starcie procesu w kolejności, której ten moduł nie ma powodu zakładać.
    """
    from apps.accounts.preferences import CONTRAST_SESSION_KEY

    session = getattr(request, "session", None)
    if session is None:
        return False
    return bool(session.get(CONTRAST_SESSION_KEY))


def _eligible(request) -> bool:
    """Czy to żądanie w ogóle wolno obsłużyć tą warstwą – tak samo dla odczytu i zapisu."""
    if request.method not in ("GET", "HEAD"):
        return False
    if not is_cacheable_path(request.path_info):
        return False
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return False
    if _query_suffix(request) is None:
        return False
    if _high_contrast_set(request):
        return False
    return True


def _messages_shown(request) -> bool:
    """Czy komunikat organizatora (``django.contrib.messages``) został **odczytany** w tym żądaniu.

    ``request.session.modified`` przychodzi za późno: ``MessageMiddleware`` konsumuje kolejkę
    i zapisuje ją z powrotem do sesji dopiero w swojej **fazie odpowiedzi**, a ta warstwa stoi niżej
    w łańcuchu (patrz docstring modułu, punkt o miejscu w ``MIDDLEWARE``) – w chwili, gdy pyta
    o ``session.modified``, tamten zapis jeszcze się nie wydarzył. Magazyn komunikatów
    (``request._messages``, ustawiony przez ``MessageMiddleware`` w fazie **żądania**) jest za to
    dostępny od razu: szablon czyta go w trakcie renderowania (``{% if messages %}``), czyli
    zanim ta warstwa w ogóle zobaczy odpowiedź z powrotem, i ustawia ``used``/``added_new``
    synchronicznie, bez czekania na cokolwiek.
    """
    storage = getattr(request, "_messages", None)
    if storage is None:
        return False
    return bool(getattr(storage, "used", False) or getattr(storage, "added_new", False))


def _storable(request, response) -> bool:
    """Czy **tę konkretną odpowiedź** wolno zapisać – dodatkowe warunki ponad ``_eligible``.

    ``response.cookies`` – nie ``response.has_header("Set-Cookie")``: ``HttpResponse.set_cookie()``
    zapisuje ciasteczko w ``response.cookies`` (``http.cookies.SimpleCookie``), a nagłówek
    ``Set-Cookie`` z niego serializuje dopiero WSGI przy wysyłce – w tym miejscu łańcucha
    (patrz docstring modułu) żadnego nagłówka jeszcze nie ma, więc ``has_header`` zawsze skłamie.
    """
    if response.status_code != 200:
        return False
    if response.cookies:
        return False
    if response.get("Vary"):
        return False
    content_type = response.get("Content-Type", "").split(";")[0].strip().lower()
    if content_type != "text/html":
        return False
    if len(response.content) > PAGE_CACHE_MAX_BYTES:
        return False
    session = getattr(request, "session", None)
    if session is not None and session.modified:
        return False
    if _messages_shown(request):
        return False
    return True


# --- Nonce CSP i token CSRF: placeholder przy zapisie, świeża wartość przy odtworzeniu ------------

NONCE_PLACEHOLDER = b"@@page-cache-nonce@@"
CSRF_PLACEHOLDER = b"@@page-cache-csrf@@"

#: Token CSRF stoi w treści dokładnie w jednym miejscu – atrybucie ``hx-headers`` znacznika
#: ``<body>`` (``templates/base.html``). Wzorzec jest wąski celowo: łapie **ten** token, a nie
#: dowolny ciąg, który przypadkiem wygląda podobnie gdzie indziej na stronie. Dokładnie jedno
#: dopasowanie jest jedynym stanem, który ta warstwa akceptuje bez wahania – gdyby redaktor kiedyś
#: przez pomyłkę wkleił drugi taki wzorzec (np. w treści strony), dwa dopasowania i tak nie mówią,
#: który podmienić, więc odpowiedź w ogóle nie trafia do cache'u (patrz ``_placeholder_body``).
_CSRF_TOKEN_RE = re.compile(rb'"X-CSRFToken":\s*"([^"]+)"')


def _placeholder_body(request, content: bytes) -> bytes | None:
    """Treść gotowa do zapisania w cache'u, albo ``None``, gdy tej odpowiedzi nie wolno zapisać.

    ``None`` wyłącznie wtedy, gdy token CSRF wystąpił **więcej niż raz** – nie wiadomo wtedy, który
    z nich podmienić na świeży przy odtworzeniu, a zostawienie nieświeżego byłoby cichym 403-ką na
    pierwszym POST-cie HTMX. Zero wystąpień jest przypadkiem poprawnym (strona bez ``hx-headers``)
    i nie blokuje zapisu.
    """
    nonce = getattr(request, "csp_nonce", "")
    if nonce:
        content = content.replace(nonce.encode(), NONCE_PLACEHOLDER)
    matches = _CSRF_TOKEN_RE.findall(content)
    if len(matches) > 1:
        return None
    if len(matches) == 1:
        content = content.replace(matches[0], CSRF_PLACEHOLDER)
    return content


def _placeholder_header(nonce: str, header: str) -> str:
    return header.replace(nonce, NONCE_PLACEHOLDER.decode()) if nonce else header


def _materialize_body(request, content: bytes) -> bytes:
    nonce = getattr(request, "csp_nonce", "")
    if nonce:
        content = content.replace(NONCE_PLACEHOLDER, nonce.encode())
    if CSRF_PLACEHOLDER in content:
        # ``get_token`` – ta sama funkcja publiczna, na której stoi ``@ensure_csrf_cookie`` – zwraca
        # świeżo zamaskowany token i oznacza w ``request.META``, że ciasteczko ma zostać odświeżone;
        # samo ciasteczko dokłada ``CsrfViewMiddleware`` w swojej fazie odpowiedzi, wyżej w łańcuchu
        # (patrz docstring modułu, punkt o miejscu w ``MIDDLEWARE``).
        content = content.replace(CSRF_PLACEHOLDER, get_token(request).encode())
    return content


def _materialize_header(nonce: str, header: str) -> str:
    return header.replace(NONCE_PLACEHOLDER.decode(), nonce) if nonce else header


# --- Metryki (opcjonalne, tanie) -------------------------------------------------------------------


def _increment_metric(name: str) -> None:
    key = f"{METRIC_PREFIX}:{name}"
    if not _safe_incr(key):
        _safe_set(key, 1, None)


def metrics() -> dict:
    """Liczniki trafień/chybień od ostatniego wyczyszczenia cache'a – do strony statusu."""
    return {
        "hits": _safe_get(f"{METRIC_PREFIX}:hit") or 0,
        "misses": _safe_get(f"{METRIC_PREFIX}:miss") or 0,
    }


# --- Middleware -------------------------------------------------------------------------------------


class PageCacheMiddleware:
    """Serwuje i wypełnia cache stron publicznych. Miejsce w łańcuchu – patrz docstring modułu."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not feature_enabled() or not _eligible(request):
            response = self.get_response(request)
            response.setdefault("X-Page-Cache", "BYPASS")
            return response

        key = build_key(request)
        if key is None:  # pragma: no cover - _eligible już odrzuca te same przypadki
            response = self.get_response(request)
            response.setdefault("X-Page-Cache", "BYPASS")
            return response

        cached = _safe_get(key)
        if cached is not None:
            _increment_metric("hit")
            return self._serve_hit(request, cached)

        _increment_metric("miss")
        response = self.get_response(request)
        self._maybe_store(request, key, response)
        response.setdefault("X-Page-Cache", "MISS")
        response.setdefault("Cache-Control", CACHE_CONTROL_VALUE)
        return response

    def _serve_hit(self, request, cached: dict) -> HttpResponse:
        body = _materialize_body(request, cached["body"])
        response = HttpResponse(body, content_type=cached["content_type"])
        if cached.get("content_language"):
            response["Content-Language"] = cached["content_language"]
        if cached.get("csp"):
            nonce = getattr(request, "csp_nonce", "")
            response["Content-Security-Policy"] = _materialize_header(nonce, cached["csp"])
        response["X-Page-Cache"] = "HIT"
        response["Cache-Control"] = CACHE_CONTROL_VALUE
        return response

    def _maybe_store(self, request, key: str, response) -> None:
        if not _storable(request, response):
            return
        body = _placeholder_body(request, response.content)
        if body is None:
            return
        nonce = getattr(request, "csp_nonce", "")
        csp = response.get("Content-Security-Policy", "")
        payload = {
            "body": body,
            "content_type": response.get("Content-Type", "text/html"),
            "content_language": response.get("Content-Language", ""),
            "csp": _placeholder_header(nonce, csp) if csp else "",
        }
        _safe_set(key, payload, _ttl_seconds())


# --- Unieważnianie: sygnały modeli, których zmiana zmienia treść stron publicznych ----------------
#
# Import na górze tej sekcji (nie na samej górze modułu): Wagtail i modele ``apps.cms``/
# ``apps.competitions``/``apps.results`` ładują się bezpiecznie dopiero, gdy rejestr aplikacji jest
# gotowy – a ten moduł importuje wyłącznie ``apps.web.apps.WebConfig.ready()``, czyli już po tym
# fakcie (tak samo jak ``apps.cms.apps.CmsConfig.ready()`` importuje ``apps.cms.announcements`` i
# ``apps.cms.sponsor_slider`` – ten sam wzorzec, ten sam powód). ``sender`` jest wszędzie **podany
# wprost**, a nie wyłapany przez ``isinstance`` w ciele odbiornika: inaczej każdy zapis dowolnego
# modelu w całej instalacji (uczestnik, praca, recenzja – setki na minutę w szczycie) wywoływałby
# tę funkcję na próżno.

from django.db.models.signals import post_delete, post_save  # noqa: E402
from django.dispatch import receiver  # noqa: E402
from wagtail.models import Page  # noqa: E402
from wagtail.signals import page_published, page_unpublished, post_page_move  # noqa: E402

from apps.cms.models import Announcement, SiteSettings  # noqa: E402
from apps.cms.tenancy import competition_for_page, competition_for_site  # noqa: E402
from apps.competitions.models import Edition, EditionEvent, Stage  # noqa: E402
from apps.promo.models import PromoMaterial  # noqa: E402
from apps.results.models import ResultsPublication  # noqa: E402
from apps.workshop_materials.models import WorkshopMaterial  # noqa: E402


def _competition_id_for_page(page) -> int | None:
    competition = competition_for_page(page)
    return competition.pk if competition is not None else None


@receiver(page_published, dispatch_uid="web.page_cache.invalidate_on_publish")
@receiver(page_unpublished, dispatch_uid="web.page_cache.invalidate_on_unpublish")
@receiver(post_page_move, dispatch_uid="web.page_cache.invalidate_on_move")
def _on_page_changed(sender, instance, **kwargs) -> None:
    invalidate_competition(_competition_id_for_page(instance))


@receiver(post_delete, sender=Page, dispatch_uid="web.page_cache.invalidate_on_delete")
def _on_page_deleted(sender, instance, **kwargs) -> None:
    """``sender=Page``, nie podklasa: dziedziczenie wielotabelowe Wagtaila kasuje przy skasowaniu

    strony wiersz **obu** tabel (własnej i bazowej ``wagtailcore.Page``) i wysyła ``post_delete``
    dla obu – łapiemy tu wyłącznie ten drugi moment, żeby nie unieważniać dwa razy.
    """
    invalidate_competition(_competition_id_for_page(instance))


@receiver(post_save, sender=SiteSettings, dispatch_uid="web.page_cache.invalidate_on_sitesettings")
def _on_site_settings_saved(sender, instance, **kwargs) -> None:
    competition = competition_for_site(instance.site)
    invalidate_competition(competition.pk if competition is not None else None)


@receiver(post_save, sender=Announcement, dispatch_uid="web.page_cache.invalidate_on_announcement_save")
@receiver(post_delete, sender=Announcement, dispatch_uid="web.page_cache.invalidate_on_announcement_delete")
def _on_announcement_changed(sender, instance, **kwargs) -> None:
    invalidate_competition(instance.competition_id)


@receiver(post_save, sender=Edition, dispatch_uid="web.page_cache.invalidate_on_edition_save")
def _on_edition_saved(sender, instance, **kwargs) -> None:
    invalidate_competition(instance.competition_id)


@receiver(post_save, sender=Stage, dispatch_uid="web.page_cache.invalidate_on_stage_save")
def _on_stage_saved(sender, instance, **kwargs) -> None:
    invalidate_competition(instance.edition.competition_id)


@receiver(post_save, sender=EditionEvent, dispatch_uid="web.page_cache.invalidate_on_edition_event_save")
def _on_edition_event_saved(sender, instance, **kwargs) -> None:
    invalidate_competition(instance.edition.competition_id)


@receiver(
    post_save, sender=ResultsPublication, dispatch_uid="web.page_cache.invalidate_on_results_publication"
)
def _on_results_publication_saved(sender, instance, **kwargs) -> None:
    invalidate_competition(instance.stage.edition.competition_id)


@receiver(post_save, sender=PromoMaterial, dispatch_uid="web.page_cache.invalidate_on_promo_save")
@receiver(post_delete, sender=PromoMaterial, dispatch_uid="web.page_cache.invalidate_on_promo_delete")
def _on_promo_material_changed(sender, instance, **kwargs) -> None:
    """Plakat zmienia **dwie** rzeczy naraz: listę ``/plakaty/`` i odnośnik w stopce każdej strony.

    Stąd unieważnienie całej witryny konkursu, a nie samego adresu listy: strona główna zapisana
    w pamięci przed opublikowaniem pierwszego plakatu nie miałaby odnośnika w stopce przez cały
    czas życia wpisu, a po zdjęciu ostatniego – prowadziłaby w 404.
    """
    invalidate_competition(instance.competition_id)


@receiver(
    post_save, sender=WorkshopMaterial, dispatch_uid="web.page_cache.invalidate_on_workshop_material_save"
)
@receiver(
    post_delete, sender=WorkshopMaterial, dispatch_uid="web.page_cache.invalidate_on_workshop_material_delete"
)
def _on_workshop_material_changed(sender, instance, **kwargs) -> None:
    """Materiał z warsztatów zmienia zapowiedź na ``/warsztaty/`` („są materiały – zaloguj się”).

    W pamięci siedzi wyłącznie wersja strony dla **gościa**: licznik materiałów i odnośnik do
    logowania, bez żadnego podpisanego adresu (te powstają dopiero w widokach dla zalogowanych,
    których ta warstwa nie obsługuje). Unieważniamy całą witrynę konkursu, bo tak robi każdy inny
    odbiornik w tym pliku, a zapis materiału jest rzadki.
    """
    invalidate_competition(instance.competition_id)

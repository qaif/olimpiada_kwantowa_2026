"""Klient wewnętrznego API aplikacji głównej (``GET /internal/djcms/v2/…``, § 8.3 docs/tasks/DJ-01.md,
§ 4.5 docs/tasks/DJ-02.md).

API v2 jest per konkurs: ``c/<slug>/<endpoint>`` (``chrome``, ``stages``, …, ``export``) oraz jedna
lista konkursów ``competitions`` (rejestr witryn, ``apps.sites.registry``). Konkurs jest w ścieżce,
więc i w kluczu bufora (``djcms:api:v2:<slug>:<endpoint>``), i w pamięci odsłony (``(slug, endpoint)``).
Bezpiecznik (``djcms:api:breaker``) jest **wspólny** – mówi o dostępności ``web``, nie konkursu;
błąd dotyczący jednego konkursu (JSON ``{"error": "no-competition"}``) zamyka wyłącznie jego
własny bezpiecznik (``djcms:api:breaker:<slug>``), żeby nieaktywny konkurs nie gasił danych
pozostałych.

Djcms nie ma żadnej innej drogi do danych zawodów: terminy, stan rejestracji, komunikaty, pasek osi
czasu, zadania i wyniki liczy aplikacja główna tymi samymi funkcjami, co strony Wagtaila, a tu
przychodzą gotowe (JSON z napisami już sformatowanymi). Stąd cztery zasady tego modułu:

- **strona djcms nigdy nie czeka długo i nigdy nie pada przez API.** Krótki limit czasu, bufor
  świeży (``DJCMS_API_CACHE_SECONDS``) i kopia awaryjna „stale” (``DJCMS_API_STALE_SECONDS``),
  a po błędzie bezpiecznik (``DJCMS_API_BREAKER_SECONDS``): kolejne odsłony nie czekają na ten sam
  timeout, tylko od razu biorą kopię albo degradują. ``MainApi.get`` nie podnosi wyjątku –
  zwraca ``ApiResult`` z ``error``, a szablon pokazuje ramę bez danych albo komunikat
  ``dj/partials/_unavailable.html``,
- **token nie wychodzi poza jedno żądanie do ``web``.** Nagłówek ``X-Djcms-Token`` ustawiamy
  wyłącznie na żądaniu do ``DJCMS_MAIN_API_URL``; otwieracz nie korzysta z proxy ze środowiska
  i **nie** podąża za przekierowaniami (``urllib`` kopiuje nagłówki do adresu docelowego
  przekierowania – token poleciałby tam, dokąd wskazałby ``Location``). W logach jest nazwa
  endpointu i kod błędu, nigdy nagłówki ani token,
- **odpowiedź jest sprawdzana, zanim trafi do szablonu**: status 200, co najwyżej
  ``MAX_RESPONSE_BYTES``, poprawny JSON będący obiektem i ``api_version == API_VERSION``.
  Odpowiedź innej wersji kontraktu to błąd („version”), a nie „spróbujemy wyrenderować”,
- **jedno pobranie na odsłonę.** Wynik zostaje w ``request._dj_api`` – rama (``chrome``)
  i wtyczki żywe pytają klienta wielokrotnie, a bufor procesu i tak by odpowiedział, ale wtedy
  dwie części tej samej strony mogłyby pokazać dwa różne stany (świeży i po wygaśnięciu).

Bufor to ``default`` (LocMem, per proces – świadomie bez Redisa aplikacji głównej).
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO
from urllib.parse import urljoin, urlsplit

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

#: Wersja kontraktu API – ta sama liczba, co ``"api_version"`` odpowiedzi ``/internal/djcms/v2/``
#: (``backend/apps/cms/djcms_api``, DJ-02b).
API_VERSION = 2

TOKEN_HEADER = "X-Djcms-Token"

#: Górny limit odpowiedzi JSON. Największa (``results`` z kilkoma tabelami) ma dziesiątki kilobajtów;
#: limit chroni proces przed odpowiedzią, której nie powinno być (błąd konfiguracji, zły adres).
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

#: Limit **pojedynczej** operacji gniazda (rozwiązanie nazwy, połączenie, jeden odczyt). ``urllib``
#: ma jeden ``timeout`` na każdą operację, a nie na całe żądanie – całość pilnuje osobno termin
#: ``DJCMS_API_TIMEOUT`` sprawdzany między kolejnymi porcjami odczytu (§ 2: 1 s / 2 s).
CONNECT_TIMEOUT_SECONDS = 1.0

#: Czas pobierania paczki eksportu (``download_export``) – paczka z obrazami bywa duża,
#: a woła ją wyłącznie komenda importu, nie odsłona strony.
EXPORT_TIMEOUT_SECONDS = 120.0

READ_CHUNK_BYTES = 64 * 1024

CACHE_PREFIX = f"djcms:api:v{API_VERSION}:"
#: Bezpiecznik dostępności ``web`` – wspólny dla wszystkich konkursów.
BREAKER_KEY = "djcms:api:breaker"
#: Bezpiecznik jednego konkursu (``BREAKER_KEY:<slug>``) – odpowiedź-błąd dotycząca tylko jego.
SCOPED_BREAKER_PREFIX = f"{BREAKER_KEY}:"

#: Zakres listy konkursów w kluczach bufora i pamięci odsłony (slug nigdy nie zawiera ``_``).
PLATFORM_SCOPE = "_"
COMPETITIONS_ENDPOINT = "competitions"

#: Dopuszczalne nazwy endpointów konkursu: ``chrome``, ``stages``, …, ``editions/<id>/results``.
#: Nazwa trafia do adresu i do klucza bufora, więc nic spoza tego wzorca (``..``, ``?``, ``//host``)
#: nie przejdzie.
ENDPOINT_RE = re.compile(r"^[a-z]+(?:/[0-9]{1,10}/[a-z]+)?$")
#: Slug konkursu – ten sam wzorzec, co ``re_path`` API v2 po stronie aplikacji głównej.
SLUG_RE = re.compile(r"^[a-z0-9-]{1,50}$")

#: Endpointy, których stan potrafi się **cofnąć** (wycofane otwarcie etapu, wycofana publikacja
#: wyników): bufor świeży najwyżej ``SHORT_CACHE_SECONDS``, a kopia „stale” nigdy nie trafia na
#: stronę jako lista (``apps.live.data.FRESH_ONLY_ENDPOINT_RE``) – tylko komunikat „niedostępne”.
SHORT_TTL_ENDPOINT_RE = re.compile(r"^(?:problems|results|editions/[0-9]{1,10}/results)$")
#: Domyślny bufor świeży tych endpointów (``DJCMS_API_SHORT_CACHE_SECONDS`` nadpisuje).
SHORT_CACHE_SECONDS = 15

#: Limit ciała odpowiedzi-błędu, które czytamy, żeby odróżnić błąd konkursu od awarii ``web``.
ERROR_BODY_BYTES = 4 * 1024

#: Atrybut żądania z wynikami pobranymi w trakcie tej odsłony (``(slug, endpoint)`` → ``ApiResult``).
REQUEST_MEMO_ATTR = "_dj_api"
#: Atrybut żądania ustawiany, gdy któraś część strony dostała dane z kopii albo nie dostała ich
#: wcale – ``apps.live.middleware.DegradedHeaderMiddleware`` dokleja wtedy ``X-Djcms-Degraded: 1``.
REQUEST_DEGRADED_ATTR = "_dj_degraded"


class MainApiError(Exception):
    """Błąd ``download_export`` – komenda importu ma się na nim zatrzymać, nie strona."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class ApiResult:
    """Wynik jednego zapytania. ``data is None`` = brak danych, szablon degraduje."""

    data: dict | None
    stale: bool  # dane z kopii zapasowej (≤ DJCMS_API_STALE_SECONDS)
    fetched_at: datetime | None
    error: str | None  # "timeout" | "connection" | "http-404" | "breaker-open" | "bad-json" | "version" …

    @property
    def ok(self) -> bool:
        return self.data is not None

    @property
    def degraded(self) -> bool:
        """Dane z kopii albo brak danych – strona pokazuje coś innego niż stan „na teraz”."""
        return self.data is None or self.stale


def _resolve(host: str, port: int, timeout: float | None) -> list[tuple]:
    """``getaddrinfo`` z limitem czasu – ``socket`` go nie ma, a ``timeout`` gniazda go nie obejmuje.

    Po co: przy zatrzymanej usłudze ``web`` wbudowany DNS Dockera nie zna tej nazwy i odsyła
    pytanie do resolwerów hosta, a z sieci ``internal`` (bez wyjścia na świat) odpowiedź nie
    przychodzi – ``getaddrinfo`` czekało ok. 4 s, zanim ``timeout`` połączenia w ogóle zaczął
    działać. Pierwsza odsłona po awarii czekała więc dwa razy dłużej, niż obiecuje § 2 speca.

    Rozwiązanie nazwy biegnie w wątku-demonie, a my czekamy na nie najwyżej ``timeout``. Wątek,
    który nie zdąży, kończy się sam, gdy resolwer się podda (nie da się go przerwać) – nikt już
    nie czeka na jego wynik, a bezpiecznik (``DJCMS_API_BREAKER_SECONDS``) sprawia, że kolejne
    odsłony nie uruchamiają następnych.
    """
    outcome: dict[str, object] = {}
    done = threading.Event()

    def lookup() -> None:
        try:
            outcome["result"] = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            outcome["error"] = exc
        finally:
            done.set()

    threading.Thread(target=lookup, name="djcms-api-dns", daemon=True).start()
    if not done.wait(timeout):
        raise TimeoutError(f"rozwiązanie nazwy dłuższe niż {timeout} s")
    if "error" in outcome:
        raise outcome["error"]  # type: ignore[misc]
    return outcome["result"]  # type: ignore[return-value]


def _bounded_create_connection(
    address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs
):
    """``socket.create_connection`` z rozwiązaniem nazwy objętym tym samym limitem czasu."""
    host, port = address
    limit = None if timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    error: OSError | None = None
    for family, type_, proto, _canonname, sockaddr in _resolve(host, port, limit):
        sock = socket.socket(family, type_, proto)
        try:
            if limit is not None:
                sock.settimeout(limit)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error or OSError(f"brak adresu dla {host}")


class _BoundedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _bounded_create_connection


class _BoundedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _bounded_create_connection


class _BoundedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_BoundedHTTPConnection, req)


class _BoundedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_BoundedHTTPSConnection, req, context=self._context)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Przekierowanie = błąd. API nigdy nie przekierowuje, a ``urllib`` przeniósłby token dalej."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def _build_opener() -> urllib.request.OpenerDirector:
    # ``ProxyHandler({})`` – bez proxy ze zmiennych środowiskowych: token idzie wprost do ``web``
    # w sieci compose'a i nigdzie indziej. Własne obsługi HTTP(S) zastępują domyślne
    # (``build_opener`` pomija domyślną, gdy dostanie jej podklasę) – rozwiązanie nazwy mieści się
    # w limicie czasu połączenia (``_resolve``).
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _BoundedHTTPHandler(), _BoundedHTTPSHandler(), _NoRedirect()
    )


class _Failure(Exception):
    """Wewnętrzny sygnał porażki pobrania z kodem do ``ApiResult.error`` i logu.

    ``scoped`` – porażka dotyczy **jednego konkursu**: ``web`` odpowiedział, i to odpowiedzią-błędem
    w kształcie kontraktu v2 (``{"api_version": 2, "error": "no-competition"}``). Taka porażka nie
    otwiera wspólnego bezpiecznika – ``web`` działa, a pozostałe konkursy mają swoje dane.
    """

    def __init__(self, code: str, *, scoped: bool = False):
        super().__init__(code)
        self.code = code
        self.scoped = scoped


def _check_slug(competition: str) -> str:
    if not isinstance(competition, str) or not SLUG_RE.match(competition):
        raise ValueError(f"Niedozwolony slug konkursu w API: {competition!r}")
    return competition


def _api_path(endpoint: str, competition: str | None) -> str:
    """Ścieżka względem ``DJCMS_MAIN_API_URL``: ``c/<slug>/<endpoint>`` albo ``competitions``."""
    if competition is None:
        if endpoint != COMPETITIONS_ENDPOINT:
            raise ValueError(f"Endpoint {endpoint!r} wymaga konkursu (competition=…).")
        return endpoint
    if not ENDPOINT_RE.match(endpoint or ""):
        raise ValueError(f"Niedozwolona nazwa endpointu API: {endpoint!r}")
    return f"c/{_check_slug(competition)}/{endpoint}"


class MainApi:
    """Klient API. Bezstanowy – cały stan (bufor, bezpiecznik) żyje w ``cache``, więc instancję
    można tworzyć przy każdym użyciu."""

    def __init__(self, *, opener: urllib.request.OpenerDirector | None = None):
        self._opener = opener

    # --- konfiguracja ----------------------------------------------------------------------------

    @property
    def opener(self) -> urllib.request.OpenerDirector:
        if self._opener is None:
            self._opener = _build_opener()
        return self._opener

    @staticmethod
    def endpoint_url(endpoint: str, competition: str | None = None) -> str:
        path = _api_path(endpoint, competition)
        base = settings.DJCMS_MAIN_API_URL
        # Tylko ``http(s)``: ``urllib`` otworzyłby też ``file:`` czy ``ftp:`` z błędnej konfiguracji.
        if urlsplit(base).scheme not in ("http", "https"):
            raise ValueError("DJCMS_MAIN_API_URL musi być adresem http(s).")
        return urljoin(base if base.endswith("/") else f"{base}/", path)

    def _request(self, endpoint: str, competition: str | None, accept: str) -> urllib.request.Request:
        return urllib.request.Request(  # noqa: S310 - schemat http(s) sprawdza endpoint_url
            self.endpoint_url(endpoint, competition),
            headers={TOKEN_HEADER: settings.DJCMS_INTERNAL_TOKEN, "Accept": accept},
            method="GET",
        )

    # --- odczyt dla stron ------------------------------------------------------------------------

    def get(self, endpoint: str, *, competition: str, request=None) -> ApiResult:
        """Dane endpointu konkursu: pamięć odsłony → bufor świeży → API → kopia „stale” → brak danych.

        Nigdy nie podnosi wyjątku (poza błędem programisty – niedozwoloną nazwą endpointu/slugiem).
        """
        _api_path(endpoint, competition)  # walidacja nazwy przed jakimkolwiek odczytem bufora
        return self._get(competition, endpoint, request)

    def competitions(self, *, request=None) -> ApiResult:
        """Lista konkursów (``GET competitions``) – ta sama droga co ``get``: bufor, kopia, bezpiecznik."""
        return self._get(None, COMPETITIONS_ENDPOINT, request)

    def fetch_competitions(self) -> dict:
        """Lista konkursów **teraz**, z pominięciem bufora i bezpiecznika – dla komend.

        ``sync_competitions`` ma odpowiedzieć stanem aplikacji głównej z tej chwili albo zakończyć
        się błędem (``MainApiError``), a nie po cichu uzgodnić rejestr z kopią sprzed 10 minut.
        Sukces odświeża bufor (kolejne odsłony dostaną tę samą listę).
        """
        try:
            data = self._fetch_json(COMPETITIONS_ENDPOINT, None)
        except _Failure as failure:
            raise MainApiError(failure.code) from None
        self._store(self._cache_key(None, COMPETITIONS_ENDPOINT), data)
        return data

    @staticmethod
    def _cache_key(competition: str | None, endpoint: str) -> str:
        return f"{CACHE_PREFIX}{competition or PLATFORM_SCOPE}:{endpoint}"

    def _get(self, competition: str | None, endpoint: str, request) -> ApiResult:
        memo = self._memo(request)
        memo_key = (competition or PLATFORM_SCOPE, endpoint)
        if memo is not None and memo_key in memo:
            return memo[memo_key]
        result = self._resolve(competition, endpoint)
        if memo is not None:
            memo[memo_key] = result
            if result.degraded and competition is not None:
                # Znacznik dotyczy **strony** (dane zawodów na niej) – lista konkursów czytana przez
                # warstwę witryn nie jest częścią strony i jej kopia nie degraduje odsłony.
                setattr(request, REQUEST_DEGRADED_ATTR, True)
        return result

    @staticmethod
    def _memo(request) -> dict | None:
        if request is None:
            return None
        memo = getattr(request, REQUEST_MEMO_ATTR, None)
        if memo is None:
            memo = {}
            setattr(request, REQUEST_MEMO_ATTR, memo)
        return memo

    def _resolve(self, competition: str | None, endpoint: str) -> ApiResult:
        key = self._cache_key(competition, endpoint)
        label = f"{competition or PLATFORM_SCOPE}/{endpoint}"
        fresh = cache.get(key)
        if fresh is not None:
            return ApiResult(
                data=fresh["data"], stale=False, fetched_at=_parse(fresh["fetched_at"]), error=None
            )

        if cache.get(BREAKER_KEY):
            # Bezpiecznik otwarty: API przed chwilą nie odpowiedziało – nie czekamy na kolejny
            # timeout w każdej odsłonie. Bez logu: pierwszy błąd już jest w logu, a tu byłby
            # jeden wpis na każdą odsłonę przez cały czas awarii.
            return self._fallback(key, "breaker-open")
        scoped_breaker = SCOPED_BREAKER_PREFIX + (competition or PLATFORM_SCOPE)
        scoped_error = cache.get(scoped_breaker)
        if scoped_error:
            return self._fallback(key, scoped_error)

        try:
            data = self._fetch_json(endpoint, competition)
        except _Failure as failure:
            if failure.scoped:
                cache.set(scoped_breaker, failure.code, settings.DJCMS_API_BREAKER_SECONDS)
            else:
                cache.set(BREAKER_KEY, True, settings.DJCMS_API_BREAKER_SECONDS)
            logger.warning(
                "API aplikacji głównej: %s – %s (bezpiecznik%s na %s s).",
                label,
                failure.code,
                " konkursu" if failure.scoped else "",
                settings.DJCMS_API_BREAKER_SECONDS,
            )
            return self._fallback(key, failure.code)

        fetched_at = self._store(key, data, fresh_seconds(endpoint))
        return ApiResult(data=data, stale=False, fetched_at=fetched_at, error=None)

    @staticmethod
    def _store(key: str, data: dict, seconds: int | None = None) -> datetime:
        fetched_at = timezone.now()
        entry = {"data": data, "fetched_at": fetched_at.isoformat()}
        cache.set(key, entry, settings.DJCMS_API_CACHE_SECONDS if seconds is None else seconds)
        cache.set(key + ":stale", entry, settings.DJCMS_API_STALE_SECONDS)
        return fetched_at

    @staticmethod
    def _fallback(key: str, error: str) -> ApiResult:
        stale = cache.get(key + ":stale")
        if stale is not None:
            return ApiResult(
                data=stale["data"], stale=True, fetched_at=_parse(stale["fetched_at"]), error=error
            )
        return ApiResult(data=None, stale=False, fetched_at=None, error=error)

    def _fetch_json(self, endpoint: str, competition: str | None) -> dict:
        total = float(settings.DJCMS_API_TIMEOUT)
        deadline = time.monotonic() + total
        request = self._request(endpoint, competition, "application/json")
        try:
            with self.opener.open(request, timeout=min(CONNECT_TIMEOUT_SECONDS, total)) as response:
                if response.status != 200:
                    raise _Failure(f"http-{response.status}")
                body = _read_limited(response, MAX_RESPONSE_BYTES, deadline)
        except _Failure:
            raise
        except urllib.error.HTTPError as exc:
            raise _http_failure(exc) from None
        except TimeoutError:
            raise _Failure("timeout") from None
        except urllib.error.URLError as exc:
            reason = exc.reason
            raise _Failure("timeout" if isinstance(reason, TimeoutError) else "connection") from None
        except OSError, ValueError, http.client.HTTPException:
            # Zerwane połączenie, śmieciowa linia statusu, ucięta odpowiedź (``BadStatusLine``,
            # ``IncompleteRead`` – to ``HTTPException``, a nie ``OSError``) – dla strony to ten sam
            # „brak połączenia”.
            raise _Failure("connection") from None
        except Exception as exc:  # noqa: BLE001 - § 8.3: błąd API nigdy nie dochodzi do szablonu
            # Nazwa klasy, bez treści wyjątku: treść mogłaby nieść adres albo nagłówki żądania.
            logger.warning(
                "API aplikacji głównej: %s – nieoczekiwany błąd %s.",
                f"{competition or PLATFORM_SCOPE}/{endpoint}",
                type(exc).__name__,
            )
            raise _Failure("unexpected") from None
        data = _json_object(body)
        if data is None:
            raise _Failure("bad-json")
        if data.get("api_version") != API_VERSION:
            raise _Failure("version")
        return data

    # --- paczka eksportu (import treści) --------------------------------------------------------

    def default_competition(self) -> str:
        """Slug konkursu witryny domyślnej według aplikacji głównej (``platform.default_slug``)."""
        platform = self.fetch_competitions().get("platform")
        slug = platform.get("default_slug") if isinstance(platform, dict) else None
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            raise MainApiError("no-default", "lista konkursów bez poprawnego platform.default_slug")
        return slug

    def download_export(self, dest: BinaryIO, *, limit_bytes: int, competition: str | None = None) -> int:
        """Pobiera paczkę ``GET c/<slug>/export`` do ``dest``; zwraca liczbę bajtów.

        ``competition=None`` – konkurs witryny domyślnej (``platform.default_slug`` z listy
        konkursów); tak woła to ``import_cms_bundle --from-api`` sprzed importu wielowitrynowego.

        W odróżnieniu od ``get`` błąd **podnosi** ``MainApiError`` – woła to komenda importu, która
        ma się zatrzymać z czytelnym powodem, a nie zaimportować połowę paczki. Bez bufora
        i bezpiecznika: to jednorazowa operacja administracyjna.
        """
        slug = _check_slug(competition) if competition is not None else self.default_competition()
        request = self._request("export", slug, "application/zip")
        deadline = time.monotonic() + EXPORT_TIMEOUT_SECONDS
        written = 0
        try:
            with self.opener.open(request, timeout=EXPORT_TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise MainApiError(f"http-{response.status}")
                while chunk := response.read(READ_CHUNK_BYTES):
                    written += len(chunk)
                    if written > limit_bytes:
                        raise MainApiError("too-large", f"paczka większa niż {limit_bytes} B")
                    if time.monotonic() > deadline:
                        raise MainApiError(
                            "timeout", f"pobieranie dłuższe niż {EXPORT_TIMEOUT_SECONDS:.0f} s"
                        )
                    dest.write(chunk)
        except MainApiError:
            raise
        except urllib.error.HTTPError as exc:
            raise MainApiError(_http_failure(exc).code) from None
        except TimeoutError:
            raise MainApiError("timeout") from None
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
            raise MainApiError("connection", type(exc).__name__) from None
        return written


def fresh_seconds(endpoint: str) -> int:
    """Czas życia bufora świeżego endpointu: krótszy dla stanu, który potrafi się cofnąć."""
    if SHORT_TTL_ENDPOINT_RE.match(endpoint):
        short = int(getattr(settings, "DJCMS_API_SHORT_CACHE_SECONDS", SHORT_CACHE_SECONDS))
        return min(short, int(settings.DJCMS_API_CACHE_SECONDS))
    return int(settings.DJCMS_API_CACHE_SECONDS)


def _json_object(body: bytes) -> dict | None:
    try:
        data: Any = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def _http_failure(exc: urllib.error.HTTPError) -> _Failure:
    """Kod HTTP ≠ 200: odpowiedź-błąd kontraktu v2 (błąd **konkursu**) albo cokolwiek innego.

    Kontrakt v2 mówi o konkursie JSON-em ``{"api_version": 2, "error": "<kod>"}`` (404
    ``no-competition``, 503 ``no-public-url``) – wtedy wynik to ``api-<kod>`` i bezpiecznik tylko tego
    konkursu. Pusta 404 (bramka API: zły host/token) i każda inna odpowiedź to awaria dostępu do
    ``web`` jako całości – wspólny bezpiecznik. Ciało czytamy najwyżej ``ERROR_BODY_BYTES``.
    """
    try:
        body = exc.read(ERROR_BODY_BYTES) if exc.fp is not None else b""
    except Exception:  # noqa: BLE001 - ciało błędu to wyłącznie podpowiedź
        body = b""
    data = _json_object(body or b"")
    error = data.get("error") if data is not None and data.get("api_version") == API_VERSION else None
    if isinstance(error, str) and re.fullmatch(r"[a-z0-9-]{1,40}", error):
        return _Failure(f"api-{error}", scoped=True)
    return _Failure(f"http-{exc.code}")


def _read_limited(response, limit: int, deadline: float) -> bytes:
    """Czyta odpowiedź porcjami: najwyżej ``limit`` bajtów i nie dłużej niż do ``deadline``."""
    chunks: list[bytes] = []
    size = 0
    while chunk := response.read(READ_CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise _Failure("too-large")
        if time.monotonic() > deadline:
            raise _Failure("timeout")
        chunks.append(chunk)
    return b"".join(chunks)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


#: Wynik dla wywołania bez konkursu (żądanie, którego warstwa witryn nie rozstrzygnęła).
NO_COMPETITION = ApiResult(data=None, stale=False, fetched_at=None, error="no-competition")


def request_competition(request) -> str | None:
    """Slug konkursu żądania – z ``request.competition_site`` (``CompetitionSiteMiddleware``)."""
    site = getattr(request, "competition_site", None) if request is not None else None
    slug = getattr(site, "slug", None)
    return slug if isinstance(slug, str) and slug else None


def get(endpoint: str, *, request=None, competition: str | None = None) -> ApiResult:
    """Skrót dla szablonów, wtyczek i procesora kontekstu: konkurs z żądania, chyba że podany jawnie.

    Bez konkursu nie ma czego pytać – ``NO_COMPETITION`` (rama degraduje), a nie zgadywanie
    konkursu domyślnego: dane jednego konkursu nie mogą trafić na stronę drugiego.
    """
    slug = competition or request_competition(request)
    if slug is None:
        return NO_COMPETITION
    return MainApi().get(endpoint, competition=slug, request=request)

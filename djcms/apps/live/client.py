"""Klient wewnętrznego API aplikacji głównej (``GET /internal/djcms/v1/…``, § 8.3 docs/tasks/DJ-01.md).

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

#: Wersja kontraktu API – ta sama liczba, co ``API_VERSION`` w ``backend/apps/cms/djcms_api/views.py``.
API_VERSION = 1

TOKEN_HEADER = "X-Djcms-Token"

#: Górny limit odpowiedzi JSON. Największa (``results`` z kilkoma tabelami) ma dziesiątki kilobajtów;
#: limit chroni proces przed odpowiedzią, której nie powinno być (błąd konfiguracji, zły adres).
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

#: Limit **pojedynczej** operacji gniazda (połączenie, jeden odczyt). ``urllib`` ma jeden
#: ``timeout`` na każdą operację, a nie na całe żądanie – całość pilnuje osobno termin
#: ``DJCMS_API_TIMEOUT`` sprawdzany między kolejnymi porcjami odczytu (§ 2: 1 s / 2 s).
CONNECT_TIMEOUT_SECONDS = 1.0

#: Czas pobierania paczki eksportu (``download_export``) – paczka z obrazami bywa duża,
#: a woła ją wyłącznie komenda importu, nie odsłona strony.
EXPORT_TIMEOUT_SECONDS = 120.0

READ_CHUNK_BYTES = 64 * 1024

CACHE_PREFIX = f"djcms:api:v{API_VERSION}:"
BREAKER_KEY = "djcms:api:breaker"

#: Dopuszczalne nazwy endpointów: ``chrome``, ``stages``, …, ``editions/<id>/results``. Nazwa trafia
#: do adresu i do klucza bufora, więc nic spoza tego wzorca (``..``, ``?``, ``//host``) nie przejdzie.
ENDPOINT_RE = re.compile(r"^[a-z]+(?:/[0-9]{1,10}/[a-z]+)?$")

#: Atrybut żądania z wynikami pobranymi w trakcie tej odsłony (endpoint → ``ApiResult``).
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Przekierowanie = błąd. API nigdy nie przekierowuje, a ``urllib`` przeniósłby token dalej."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def _build_opener() -> urllib.request.OpenerDirector:
    # ``ProxyHandler({})`` – bez proxy ze zmiennych środowiskowych: token idzie wprost do ``web``
    # w sieci compose'a i nigdzie indziej.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


class _Failure(Exception):
    """Wewnętrzny sygnał porażki pobrania z kodem do ``ApiResult.error`` i logu."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


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
    def endpoint_url(endpoint: str) -> str:
        if not ENDPOINT_RE.match(endpoint):
            raise ValueError(f"Niedozwolona nazwa endpointu API: {endpoint!r}")
        base = settings.DJCMS_MAIN_API_URL
        # Tylko ``http(s)``: ``urllib`` otworzyłby też ``file:`` czy ``ftp:`` z błędnej konfiguracji.
        if urlsplit(base).scheme not in ("http", "https"):
            raise ValueError("DJCMS_MAIN_API_URL musi być adresem http(s).")
        return urljoin(base if base.endswith("/") else f"{base}/", endpoint)

    def _request(self, endpoint: str, accept: str) -> urllib.request.Request:
        return urllib.request.Request(  # noqa: S310 - schemat http(s) sprawdza endpoint_url
            self.endpoint_url(endpoint),
            headers={TOKEN_HEADER: settings.DJCMS_INTERNAL_TOKEN, "Accept": accept},
            method="GET",
        )

    # --- odczyt dla stron ------------------------------------------------------------------------

    def get(self, endpoint: str, *, request=None) -> ApiResult:
        """Dane endpointu: pamięć odsłony → bufor świeży → API → kopia „stale” → brak danych.

        Nigdy nie podnosi wyjątku (poza błędem programisty – niedozwoloną nazwą endpointu).
        """
        self.endpoint_url(endpoint)  # walidacja nazwy przed jakimkolwiek odczytem bufora
        memo = self._memo(request)
        if memo is not None and endpoint in memo:
            return memo[endpoint]
        result = self._resolve(endpoint)
        if memo is not None:
            memo[endpoint] = result
            if result.degraded:
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

    def _resolve(self, endpoint: str) -> ApiResult:
        key = CACHE_PREFIX + endpoint
        fresh = cache.get(key)
        if fresh is not None:
            return ApiResult(
                data=fresh["data"], stale=False, fetched_at=_parse(fresh["fetched_at"]), error=None
            )

        if cache.get(BREAKER_KEY):
            # Bezpiecznik otwarty: API przed chwilą nie odpowiedziało – nie czekamy na kolejny
            # timeout w każdej odsłonie. Bez logu: pierwszy błąd już jest w logu, a tu byłby
            # jeden wpis na każdą odsłonę przez cały czas awarii.
            return self._fallback(endpoint, "breaker-open")

        try:
            data = self._fetch_json(endpoint)
        except _Failure as failure:
            cache.set(BREAKER_KEY, True, settings.DJCMS_API_BREAKER_SECONDS)
            logger.warning(
                "API aplikacji głównej: %s – %s (bezpiecznik na %s s).",
                endpoint,
                failure.code,
                settings.DJCMS_API_BREAKER_SECONDS,
            )
            return self._fallback(endpoint, failure.code)

        fetched_at = timezone.now()
        entry = {"data": data, "fetched_at": fetched_at.isoformat()}
        cache.set(key, entry, settings.DJCMS_API_CACHE_SECONDS)
        cache.set(key + ":stale", entry, settings.DJCMS_API_STALE_SECONDS)
        return ApiResult(data=data, stale=False, fetched_at=fetched_at, error=None)

    @staticmethod
    def _fallback(endpoint: str, error: str) -> ApiResult:
        stale = cache.get(CACHE_PREFIX + endpoint + ":stale")
        if stale is not None:
            return ApiResult(
                data=stale["data"], stale=True, fetched_at=_parse(stale["fetched_at"]), error=error
            )
        return ApiResult(data=None, stale=False, fetched_at=None, error=error)

    def _fetch_json(self, endpoint: str) -> dict:
        total = float(settings.DJCMS_API_TIMEOUT)
        deadline = time.monotonic() + total
        request = self._request(endpoint, "application/json")
        try:
            with self.opener.open(request, timeout=min(CONNECT_TIMEOUT_SECONDS, total)) as response:
                if response.status != 200:
                    raise _Failure(f"http-{response.status}")
                body = _read_limited(response, MAX_RESPONSE_BYTES, deadline)
        except _Failure:
            raise
        except urllib.error.HTTPError as exc:
            raise _Failure(f"http-{exc.code}") from None
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
            logger.warning("API aplikacji głównej: %s – nieoczekiwany błąd %s.", endpoint, type(exc).__name__)
            raise _Failure("unexpected") from None
        try:
            data: Any = json.loads(body.decode("utf-8"))
        except UnicodeDecodeError, ValueError:
            raise _Failure("bad-json") from None
        if not isinstance(data, dict):
            raise _Failure("bad-json")
        if data.get("api_version") != API_VERSION:
            raise _Failure("version")
        return data

    # --- paczka eksportu (import treści, DJ-01g) --------------------------------------------------

    def download_export(self, dest: BinaryIO, *, limit_bytes: int) -> int:
        """Pobiera paczkę ``GET export`` do ``dest``; zwraca liczbę bajtów.

        W odróżnieniu od ``get`` błąd **podnosi** ``MainApiError`` – woła to komenda importu, która
        ma się zatrzymać z czytelnym powodem, a nie zaimportować połowę paczki. Bez bufora
        i bezpiecznika: to jednorazowa operacja administracyjna.
        """
        request = self._request("export", "application/zip")
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
            raise MainApiError(f"http-{exc.code}") from None
        except TimeoutError:
            raise MainApiError("timeout") from None
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as exc:
            raise MainApiError("connection", type(exc).__name__) from None
        return written


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


def get(endpoint: str, *, request=None) -> ApiResult:
    """Skrót dla szablonów, wtyczek i procesora kontekstu."""
    return MainApi().get(endpoint, request=request)

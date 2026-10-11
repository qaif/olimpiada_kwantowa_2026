"""Klient API aplikacji głównej (§ 8.3 docs/tasks/DJ-01.md, API v2 – § 4.5 docs/tasks/DJ-02.md).

Otwieracz ``urllib`` jest podstawiony (``main_api`` w ``conftest.py``) – testy sprawdzają zachowanie
klienta na każdą porażkę: timeout, kod HTTP, zły JSON, zła wersja kontraktu, za duża odpowiedź,
przekierowanie, bezpiecznik, bufor świeży i kopia „stale”, oraz to, że token jedzie wyłącznie
w nagłówku żądania i nigdy nie trafia do logu.
"""

import io
import logging
import urllib.error
from unittest import mock

import pytest
from django.core.cache import cache
from django.test import RequestFactory
from django.utils import timezone

from apps.live import client
from apps.live.client import ApiResult, MainApi, MainApiError

TOKEN = "t" * 48  # config/settings/test.py
SLUG = "kwantowa"
V2 = "http://web:8000/internal/djcms/v2/"

#: Prawdziwy otwieracz – zapamiętany przy imporcie, zanim fixture ``main_api`` go podmieni.
REAL_BUILD_OPENER = client._build_opener


def _api():
    return MainApi()


# --- ścieżka szczęśliwa ------------------------------------------------------------------------


def test_get_returns_data_and_sends_token_header(main_api):
    main_api.set("chrome", {"site": {"site_name": "X"}})
    result = _api().get("chrome", competition=SLUG)
    assert result.ok
    assert result.data["site"] == {"site_name": "X"}
    assert result.stale is False
    assert result.error is None
    assert result.fetched_at is not None
    request = main_api.requests[0]
    assert request.full_url == V2 + "c/kwantowa/chrome"
    assert request.get_header("X-djcms-token") == TOKEN
    assert request.get_header("Accept") == "application/json"
    assert request.get_method() == "GET"
    # Pojedyncza operacja gniazda ≤ 1 s (§ 2), całość pilnowana osobno.
    assert main_api.timeouts == [client.CONNECT_TIMEOUT_SECONDS]


def test_nested_endpoint_url(main_api):
    main_api.set("editions/7/results", {"links": []})
    assert _api().get("editions/7/results", competition=SLUG).ok
    assert main_api.requests[0].full_url == V2 + "c/kwantowa/editions/7/results"


@pytest.mark.parametrize(
    "endpoint", ["../admin", "chrome?x=1", "//evil.example/x", "Chrome", "", "editions/x/results"]
)
def test_endpoint_name_is_validated(endpoint, main_api):
    with pytest.raises(ValueError):
        _api().get(endpoint, competition=SLUG)
    assert main_api.requests == []


def test_fresh_cache_serves_without_second_request(main_api):
    main_api.set("stages", {"rows": []})
    first = _api().get("stages", competition=SLUG)
    second = _api().get("stages", competition=SLUG)
    assert first.data == second.data
    assert main_api.calls("stages") == 1
    assert second.stale is False


def test_per_request_memo_and_no_degraded_flag_on_success(main_api):
    main_api.set("chrome", {"site": {}})
    request = RequestFactory().get("/")
    first = client.get("chrome", request=request, competition=SLUG)
    cache.clear()  # nawet bez bufora procesu ta sama odsłona nie pyta drugi raz
    second = client.get("chrome", request=request, competition=SLUG)
    assert first is second
    assert main_api.calls("chrome") == 1
    assert not getattr(request, client.REQUEST_DEGRADED_ATTR, False)


# --- porażki -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (TimeoutError("timed out"), "timeout"),
        (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
        (urllib.error.URLError(ConnectionRefusedError()), "connection"),
        (ConnectionResetError(), "connection"),
        (urllib.error.HTTPError("http://web:8000/x", 404, "Not Found", {}, io.BytesIO(b"")), "http-404"),
        (urllib.error.HTTPError("http://web:8000/x", 503, "Unavailable", {}, io.BytesIO(b"")), "http-503"),
    ],
)
def test_transport_failures_degrade_without_exception(exc, code, main_api):
    main_api.fail("chrome", exc)
    result = _api().get("chrome", competition=SLUG)
    assert result == ApiResult(data=None, stale=False, fetched_at=None, error=code)
    assert result.degraded


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"<html>nie json</html>", "bad-json"),
        (b"\xff\xfe", "bad-json"),
        (b"[1, 2]", "bad-json"),
        (b'{"api_version": 1}', "version"),
        (b'{"site": {}}', "version"),
    ],
)
def test_invalid_payload_is_rejected(raw, code, main_api):
    main_api.set("chrome", raw=raw)
    assert _api().get("chrome", competition=SLUG).error == code


def test_http_client_errors_are_connection_failures(main_api):
    import http.client

    main_api.fail("chrome", http.client.BadStatusLine("śmieci"))
    assert _api().get("chrome", competition=SLUG).error == "connection"
    cache.clear()

    def _cut():
        raise http.client.IncompleteRead(b"{")

    main_api.set("chrome", {"x": 1}, on_read=_cut)
    assert _api().get("chrome", competition=SLUG).error == "connection"


def test_unexpected_error_never_reaches_the_page(main_api):
    main_api.fail("chrome", RuntimeError("coś zupełnie innego"))
    assert _api().get("chrome", competition=SLUG).error == "unexpected"


def test_non_200_success_status_is_an_error(main_api):
    main_api.set("chrome", {"x": 1}, status=204)
    assert _api().get("chrome", competition=SLUG).error == "http-204"


def test_response_size_limit(main_api, monkeypatch):
    monkeypatch.setattr(client, "MAX_RESPONSE_BYTES", 100)
    main_api.set("chrome", {"pad": "x" * 500})
    assert _api().get("chrome", competition=SLUG).error == "too-large"


def test_total_deadline_is_enforced_between_reads(main_api, monkeypatch, settings):
    settings.DJCMS_API_TIMEOUT = 2.0
    clock = iter([100.0, 103.0, 103.5, 104.0])
    monkeypatch.setattr(client.time, "monotonic", lambda: next(clock))
    main_api.set("chrome", {"x": 1})
    assert _api().get("chrome", competition=SLUG).error == "timeout"


def test_redirect_is_refused_so_the_token_does_not_follow_it():
    # ``urllib`` kopiuje nagłówki do adresu z ``Location`` – przekierowanie ma być błędem.
    request = mock.Mock(full_url=V2 + "c/kwantowa/chrome")
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        client._NoRedirect().redirect_request(request, None, 302, "Found", {}, "http://evil.example/")
    assert excinfo.value.code == 302


def test_real_opener_has_no_proxy_and_refuses_redirects(monkeypatch):
    monkeypatch.undo()  # prawdziwy ``_build_opener`` zamiast fałszywego z fixture'a (bez otwierania sieci)
    opener = client._build_opener()
    assert any(isinstance(h, client._NoRedirect) for h in opener.handlers)
    assert not any(type(h) is client.urllib.request.HTTPRedirectHandler for h in opener.handlers)
    # ``ProxyHandler({})`` nie rejestruje żadnego ``*_open`` i blokuje domyślny ProxyHandler
    # (ze zmiennych ``http_proxy``) – w otwieraczu nie zostaje nic, co kierowałoby przez proxy.
    assert not any(isinstance(h, client.urllib.request.ProxyHandler) and h.proxies for h in opener.handlers)
    # Domyślne obsługi HTTP(S) zastąpione wersjami z limitem czasu na rozwiązanie nazwy.
    assert any(isinstance(h, client._BoundedHTTPHandler) for h in opener.handlers)
    assert not any(type(h) is client.urllib.request.HTTPHandler for h in opener.handlers)


# --- rozwiązanie nazwy w limicie czasu ------------------------------------------------------------


@pytest.fixture
def hanging_dns(monkeypatch):
    """``getaddrinfo``, które nie odpowiada – jak DNS Dockera pytany o nazwę zatrzymanej usługi."""
    import threading

    release = threading.Event()
    calls = []

    def getaddrinfo(host, *args, **kwargs):
        calls.append(host)
        release.wait(10)
        raise OSError("resolver: brak odpowiedzi")

    monkeypatch.setattr(client.socket, "getaddrinfo", getaddrinfo)
    yield calls
    release.set()  # wątek-demon kończy się od razu po teście


def test_name_resolution_is_bounded_by_timeout(hanging_dns):
    import time

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        client._resolve("web", 8000, 0.2)
    assert time.monotonic() - started < 1.0
    assert hanging_dns == ["web"]


def test_resolution_errors_and_results_pass_through(monkeypatch):
    monkeypatch.setattr(client.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 8000))])
    assert client._resolve("web", 8000, 1.0) == [(2, 1, 6, "", ("10.0.0.5", 8000))]

    def fail(*args, **kwargs):
        raise client.socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(client.socket, "getaddrinfo", fail)
    with pytest.raises(client.socket.gaierror):
        client._resolve("web", 8000, 1.0)


def test_page_request_with_dead_dns_fails_within_connect_timeout(monkeypatch, hanging_dns, settings):
    """Całe żądanie – z rozwiązaniem nazwy – mieści się w limicie; wcześniej DNS dokładał ~4 s."""
    import time

    settings.DJCMS_API_TIMEOUT = 0.3
    monkeypatch.setattr(client, "_build_opener", REAL_BUILD_OPENER)
    started = time.monotonic()
    result = _api().get("chrome", competition=SLUG)
    assert result.error == "timeout"
    assert time.monotonic() - started < 1.5
    assert hanging_dns == ["web"]


def test_bounded_connection_connects_to_resolved_address():
    """``_bounded_create_connection`` łączy się z adresem z ``_resolve`` (prawdziwe gniazdo lokalne)."""
    import socket

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        sock = client._bounded_create_connection(("127.0.0.1", server.getsockname()[1]), 1.0)
        sock.close()
    finally:
        server.close()


# --- bezpiecznik i kopia „stale” ----------------------------------------------------------------


def test_breaker_skips_network_after_failure(main_api):
    main_api.fail("chrome", urllib.error.URLError(ConnectionRefusedError()))
    assert _api().get("chrome", competition=SLUG).error == "connection"
    main_api.set("chrome", {"x": 1})  # API już wstało, ale bezpiecznik jeszcze trzyma
    result = _api().get("chrome", competition=SLUG)
    assert result.error == "breaker-open"
    assert result.data is None
    assert main_api.calls("chrome") == 1


def test_breaker_expires(main_api, settings):
    main_api.fail("chrome", TimeoutError())
    _api().get("chrome", competition=SLUG)
    cache.delete(client.BREAKER_KEY)  # upływ DJCMS_API_BREAKER_SECONDS
    main_api.set("chrome", {"x": 1})
    assert _api().get("chrome", competition=SLUG).ok


def test_stale_copy_is_served_when_fresh_expired_and_api_down(main_api):
    main_api.set("chrome", {"site": {"site_name": "Stara"}})
    ok = _api().get("chrome", competition=SLUG)
    cache.delete(client.MainApi._cache_key(SLUG, "chrome"))  # świeży bufor wygasł, kopia „stale” żyje dłużej
    main_api.fail("chrome", TimeoutError())
    result = _api().get("chrome", competition=SLUG)
    assert result.stale is True
    assert result.error == "timeout"
    assert result.data == ok.data
    assert result.fetched_at == ok.fetched_at
    assert result.degraded


def test_stale_copy_is_also_used_while_breaker_is_open(main_api):
    main_api.set("stages", {"rows": [1]})
    _api().get("stages", competition=SLUG)
    cache.delete(client.MainApi._cache_key(SLUG, "stages"))
    cache.set(client.BREAKER_KEY, True, 15)
    result = _api().get("stages", competition=SLUG)
    assert (result.stale, result.error, result.data["rows"]) == (True, "breaker-open", [1])


def test_cache_timeouts_follow_settings(main_api, settings):
    settings.DJCMS_API_CACHE_SECONDS = 60
    settings.DJCMS_API_STALE_SECONDS = 600
    main_api.set("chrome", {"x": 1})
    with mock.patch.object(client.cache, "set", wraps=client.cache.set) as spy:
        _api().get("chrome", competition=SLUG)
    assert [(c.args[0], c.args[2]) for c in spy.call_args_list] == [
        (client.MainApi._cache_key(SLUG, "chrome"), 60),
        (client.MainApi._cache_key(SLUG, "chrome") + ":stale", 600),
    ]


def test_degraded_request_is_marked(main_api):
    request = RequestFactory().get("/")
    client.get("chrome", request=request, competition=SLUG)
    assert getattr(request, client.REQUEST_DEGRADED_ATTR) is True


# --- logi ---------------------------------------------------------------------------------------


def test_failure_is_logged_once_without_token(main_api, caplog):
    main_api.fail(
        "chrome",
        urllib.error.HTTPError(
            V2 + "c/kwantowa/chrome",
            404,
            "Not Found",
            {"X-Djcms-Token": TOKEN},
            io.BytesIO(b""),
        ),
    )
    with caplog.at_level(logging.DEBUG, logger="apps.live.client"):
        _api().get("chrome", competition=SLUG)
        _api().get("chrome", competition=SLUG)  # bezpiecznik – bez kolejnego wpisu
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "chrome" in warnings[0].getMessage() and "http-404" in warnings[0].getMessage()
    assert TOKEN not in caplog.text
    assert TOKEN[:8] not in caplog.text


# --- ApiResult i „stan na” -------------------------------------------------------------------------


def test_stale_label():
    from apps.live.chrome import stale_label

    fetched = timezone.make_aware(timezone.datetime(2026, 9, 26, 12, 5), timezone.get_fixed_timezone(120))
    assert stale_label(ApiResult({"a": 1}, True, fetched, "timeout")) == "stan na 12:05"
    assert stale_label(ApiResult({"a": 1}, False, fetched, None)) == ""
    assert stale_label(None) == ""


# --- paczka eksportu ----------------------------------------------------------------------------


def test_download_export_streams_to_destination(main_api):
    main_api.set("export", raw=b"PK\x03\x04" + b"x" * 1000)
    dest = io.BytesIO()
    assert _api().download_export(dest, limit_bytes=10_000, competition=SLUG) == 1004
    assert dest.getvalue().startswith(b"PK")
    request = main_api.requests[0]
    assert request.get_header("Accept") == "application/zip"
    assert request.get_header("X-djcms-token") == TOKEN
    assert main_api.timeouts == [client.EXPORT_TIMEOUT_SECONDS]


def test_download_export_enforces_limit(main_api):
    main_api.set("export", raw=b"x" * (client.READ_CHUNK_BYTES * 2))
    with pytest.raises(MainApiError) as excinfo:
        _api().download_export(io.BytesIO(), limit_bytes=client.READ_CHUNK_BYTES, competition=SLUG)
    assert excinfo.value.code == "too-large"


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (urllib.error.HTTPError("http://web:8000/x", 404, "Not Found", {}, io.BytesIO(b"")), "http-404"),
        (TimeoutError(), "timeout"),
        (urllib.error.URLError(ConnectionRefusedError()), "connection"),
    ],
)
def test_download_export_raises_on_failure(exc, code, main_api):
    main_api.fail("export", exc)
    with pytest.raises(MainApiError) as excinfo:
        _api().download_export(io.BytesIO(), limit_bytes=10, competition=SLUG)
    assert excinfo.value.code == code
    assert TOKEN not in str(excinfo.value)


# --- API v2: konkurs w ścieżce, lista konkursów, bezpieczniki (DJ-02 § 4.5) --------------------------


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(V2 + "c/x/chrome", code, "Error", {}, io.BytesIO(body))


@pytest.mark.parametrize("slug", ["../kwantowa", "KWANTOWA", "a" * 51, "a/b", "", "kw_antowa"])
def test_competition_slug_is_validated(slug, main_api):
    with pytest.raises(ValueError):
        _api().get("chrome", competition=slug)
    assert main_api.requests == []


def test_competition_endpoints_require_a_competition():
    with pytest.raises(ValueError):
        MainApi.endpoint_url("chrome")
    assert MainApi.endpoint_url("competitions") == V2 + "competitions"
    assert MainApi.endpoint_url("export", "fizyka") == V2 + "c/fizyka/export"


def test_cache_and_memo_are_per_competition(main_api):
    main_api.set("chrome", {"site": {"site_name": "A"}})
    main_api.set("chrome", {"site": {"site_name": "B"}}, competition="fizyka")
    request = RequestFactory().get("/")
    first = client.get("chrome", request=request, competition=SLUG)
    second = client.get("chrome", request=request, competition="fizyka")
    assert (first.data["site"]["site_name"], second.data["site"]["site_name"]) == ("A", "B")
    assert set(request._dj_api) == {(SLUG, "chrome"), ("fizyka", "chrome")}
    assert cache.get("djcms:api:v2:kwantowa:chrome")["data"]["site"] == {"site_name": "A"}
    assert cache.get("djcms:api:v2:fizyka:chrome")["data"]["site"] == {"site_name": "B"}


def test_competitions_list_has_its_own_cache_key(main_api):
    main_api.set("competitions", {"competitions": []}, competition=None)
    assert _api().competitions().ok
    assert main_api.requests[0].full_url == V2 + "competitions"
    assert cache.get("djcms:api:v2:_:competitions") is not None
    assert _api().competitions().ok
    assert main_api.calls("competitions", competition=None) == 1


def test_competition_error_body_trips_only_that_competitions_breaker(main_api):
    body = b'{"api_version": 2, "error": "no-competition"}'
    main_api.fail("chrome", _http_error(404, body), competition="uspiona")
    main_api.set("chrome", {"x": 1})
    assert _api().get("chrome", competition="uspiona").error == "api-no-competition"
    assert cache.get(client.BREAKER_KEY) is None
    # Drugi konkurs dostaje dane – ``web`` działa, błąd dotyczył tylko jednego konkursu.
    assert _api().get("chrome", competition=SLUG).ok
    # Ten sam konkurs nie pyta ponownie, dopóki trzyma jego bezpiecznik.
    assert _api().get("stages", competition="uspiona").error == "api-no-competition"
    assert main_api.calls("stages", competition="uspiona") == 0


def test_no_public_url_is_a_competition_error(main_api):
    main_api.fail("chrome", _http_error(503, b'{"api_version": 2, "error": "no-public-url"}'))
    assert _api().get("chrome", competition=SLUG).error == "api-no-public-url"
    assert cache.get(client.BREAKER_KEY) is None


@pytest.mark.parametrize(
    "body",
    [b"", b"<html>404</html>", b'{"api_version": 1, "error": "no-competition"}', b'{"error": "a b"}'],
)
def test_gate_404_and_foreign_errors_trip_the_shared_breaker(body, main_api):
    main_api.fail("chrome", _http_error(404, body))
    main_api.set("chrome", {"x": 1}, competition="fizyka")
    assert _api().get("chrome", competition=SLUG).error == "http-404"
    assert cache.get(client.BREAKER_KEY)
    assert _api().get("chrome", competition="fizyka").error == "breaker-open"


def test_fetch_competitions_bypasses_cache_and_breaker(main_api):
    cache.set(client.BREAKER_KEY, True, 15)
    main_api.set("competitions", {"competitions": [{"slug": "a"}]}, competition=None)
    assert _api().fetch_competitions()["competitions"] == [{"slug": "a"}]
    # Świeża lista trafia do bufora – odsłony dostaną tę samą.
    assert _api().competitions().data["competitions"] == [{"slug": "a"}]


def test_fetch_competitions_uses_command_timeout_not_page_timeout(main_api, settings):
    """OPS-04 § 4: komenda tuż po restarcie ``web`` nie może dostać limitu odsłony (1 s na gniazdo)."""
    settings.DJCMS_API_TIMEOUT = 2.0
    main_api.set("competitions", {"competitions": []}, competition=None)
    _api().fetch_competitions()
    assert main_api.timeouts == [client.COMMAND_TIMEOUT_SECONDS]
    assert client.COMMAND_TIMEOUT_SECONDS >= 10
    # Odsłona strony – bez zmian: sekunda na operację gniazda.
    main_api.set("chrome", {"x": 1})
    _api().get("chrome", competition=SLUG)
    assert main_api.timeouts[-1] == client.CONNECT_TIMEOUT_SECONDS


def test_fetch_competitions_deadline_is_the_command_timeout(main_api, monkeypatch, settings):
    # Odczyt 3 s po starcie: odsłona (DJCMS_API_TIMEOUT=2) skończyłaby się „timeout”, komenda – nie.
    settings.DJCMS_API_TIMEOUT = 2.0
    clock = iter([100.0, 103.0, 103.5, 104.0])
    monkeypatch.setattr(client.time, "monotonic", lambda: next(clock))
    main_api.set("competitions", {"competitions": [{"slug": "a"}]}, competition=None)
    assert _api().fetch_competitions()["competitions"] == [{"slug": "a"}]


def test_fetch_competitions_raises_on_failure(main_api):
    main_api.fail("competitions", TimeoutError(), competition=None)
    with pytest.raises(MainApiError) as excinfo:
        _api().fetch_competitions()
    assert excinfo.value.code == "timeout"


def test_download_export_without_competition_uses_the_default_one(main_api):
    main_api.set(
        "competitions", {"platform": {"default_slug": "kwantowa"}, "competitions": []}, competition=None
    )
    main_api.set("export", raw=b"PK\x03\x04")
    assert _api().download_export(io.BytesIO(), limit_bytes=100) == 4
    assert main_api.requests[-1].full_url == V2 + "c/kwantowa/export"


@pytest.mark.parametrize("platform", [{}, {"default_slug": None}, {"default_slug": "../x"}])
def test_download_export_without_default_competition_fails(platform, main_api):
    main_api.set("competitions", {"platform": platform, "competitions": []}, competition=None)
    with pytest.raises(MainApiError) as excinfo:
        _api().download_export(io.BytesIO(), limit_bytes=100)
    assert excinfo.value.code == "no-default"
    assert main_api.calls("export") == 0


def test_shortcut_takes_the_competition_from_the_request(main_api):
    from types import SimpleNamespace

    main_api.set("chrome", {"x": 1}, competition="fizyka")
    request = RequestFactory().get("/")
    request.competition_site = SimpleNamespace(slug="fizyka")
    assert client.get("chrome", request=request).data["x"] == 1
    assert main_api.requests[0].full_url == V2 + "c/fizyka/chrome"


def test_shortcut_without_competition_asks_nothing(main_api):
    result = client.get("chrome", request=RequestFactory().get("/"))
    assert result is client.NO_COMPETITION
    assert not result.ok
    assert main_api.requests == []


def test_degraded_competitions_list_does_not_mark_the_page(main_api):
    main_api.fail("competitions", urllib.error.URLError(ConnectionRefusedError()), competition=None)
    request = RequestFactory().get("/")
    assert _api().competitions(request=request).error == "connection"
    assert not getattr(request, client.REQUEST_DEGRADED_ATTR, False)

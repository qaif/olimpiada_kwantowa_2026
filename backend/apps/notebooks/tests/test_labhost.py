"""Laboratorium na osobnym hoście (QC-02): rozdział hostów, notatnik startowy bez sesji, strażnik
żądań z laboratorium na hostach serwisu, ustawienia i ciasteczka host-only."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core import signing
from django.test import Client

from apps.accounts.models import CompetitionRole
from apps.notebooks import cookieguard, services
from apps.notebooks.checks import lab_host_is_separate
from apps.notebooks.middleware import FORBIDDEN_TEXT
from apps.tenancy.tests.factories import grant_membership
from apps.web.middleware import NOTEBOOK_LAB_HEADERS, build_notebook_lab_policy

from .conftest import HIDDEN_SENTINEL, enable
from .test_views import configured

pytestmark = pytest.mark.django_db

LAB = "lab.example.test"
LAB_PATH = "/static/notebook-lab/314.0.7-abc/lab/index.html"


@pytest.fixture
def lab_host(settings):
    settings.NOTEBOOK_LAB_HOST = LAB
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, LAB]
    return LAB


@pytest.fixture
def lab_built(settings, tmp_path):
    root = tmp_path / "lab"
    (root / "314.0.7-abc" / "lab").mkdir(parents=True)
    (root / "314.0.7-abc" / "lab" / "index.html").write_text("<!doctype html><title>lab</title>")
    (root / "current.json").write_text(json.dumps({"build_id": "314.0.7-abc", "transfer_bytes": 1}))
    settings.NOTEBOOK_LAB_DIR = str(root)
    from apps.notebooks import lab

    lab._cache.clear()
    return root


def on_lab(client, path, **extra):
    return client.get(path, HTTP_HOST=LAB, **extra)


def lab_token_path(problem, participant):
    return services.starter_url(services.task_for(problem), participant, participant.user)


# --- rozdział hostów ------------------------------------------------------------------------------


def test_without_setting_nothing_changes(settings, competition, coordinator, problem, participant):
    """Bez ``NOTEBOOK_LAB_HOST`` – QC-01: ścieżka laboratorium i notatnik startowy na hoście serwisu."""
    assert settings.NOTEBOOK_LAB_HOST == ""
    enable(competition)
    configured(problem, coordinator)
    client = Client()
    response = client.get(LAB_PATH)
    assert response.status_code == 404 and "Cross-Origin-Embedder-Policy" in response
    client.force_login(participant.user)
    path = lab_token_path(problem, participant)
    assert client.get(path).status_code == 200


def test_platform_host_redirects_lab_and_refuses_starter(
    lab_host, competition, coordinator, problem, participant
):
    enable(competition)
    configured(problem, coordinator)
    client = Client()
    client.force_login(participant.user)
    response = client.get(f"{LAB_PATH}?fromURL=/notebook-starter/x/y.ipynb")
    assert response.status_code == 302
    assert response["Location"] == f"http://{LAB}{LAB_PATH}?fromURL=/notebook-starter/x/y.ipynb"
    # Notatnik startowy – wyłącznie na hoście laboratorium, także z ważnym tokenem i sesją.
    path = lab_token_path(problem, participant)
    response = client.get(path)
    assert response.status_code == 404
    assert HIDDEN_SENTINEL not in response.content.decode()


@pytest.mark.parametrize(
    "path", ["/", "/me/", "/api/competitions/", "/admin/", "/coordinator/notebooks/", "/healthz/"]
)
def test_lab_host_serves_nothing_from_the_platform(lab_host, participant, path):
    client = Client()
    client.force_login(participant.user)  # test-klient wysyła ciasteczko sesji niezależnie od hosta
    response = on_lab(client, path)
    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/plain")
    assert response["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'; sandbox"
    assert response["Referrer-Policy"] == "strict-origin"
    assert response["Cross-Origin-Resource-Policy"] == "same-origin"
    assert not response.cookies  # ani sesji, ani csrftoken – host laboratorium nie dotyka warstw serwisu


def test_lab_host_missing_lab_file_is_bare_404_with_lab_policy(lab_host):
    response = on_lab(Client(), "/static/notebook-lab/nie-ma/index.html")
    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/plain") and not response.cookies
    assert response["Content-Security-Policy"] == build_notebook_lab_policy(f"http://{LAB}")
    for name, value in NOTEBOOK_LAB_HEADERS.items():
        assert response[name] == value
    assert response["Referrer-Policy"] == "strict-origin"


def test_lab_files_served_only_on_lab_host(settings, lab_host, lab_built):
    """Dev (WhiteNoise): plik laboratorium z polityką originu laboratorium; host serwisu – 302."""
    settings.STATICFILES_DIRS = [*settings.STATICFILES_DIRS, ("notebook-lab", str(lab_built))]
    settings.WHITENOISE_USE_FINDERS = True
    settings.WHITENOISE_AUTOREFRESH = True
    client = Client()
    response = on_lab(client, LAB_PATH)
    assert response.status_code == 200
    assert b"<title>lab</title>" in b"".join(response.streaming_content)
    policy = response["Content-Security-Policy"]
    assert policy == build_notebook_lab_policy(f"http://{LAB}")
    assert f"connect-src http://{LAB}/static/notebook-lab/ http://{LAB}/notebook-starter/" in policy
    assert response["Cross-Origin-Embedder-Policy"] == "require-corp"
    assert client.get(LAB_PATH).status_code == 302


# --- strona zadania i notatnik startowy na hoście laboratorium -------------------------------------


def test_lab_page_links_to_lab_host(lab_host, lab_built, competition, coordinator, problem, participant):
    enable(competition)
    configured(problem, coordinator)
    client = Client()
    client.force_login(participant.user)
    html = client.get(f"/me/notebooks/{problem.pk}/").content.decode()
    assert (
        f'href="http://{LAB}/static/notebook-lab/314.0.7-abc/lab/index.html?fromURL=/notebook-starter/'
        in html
    )
    assert f'href="http://{LAB}/notebook-starter/' in html and "?download=1" in html
    assert 'target="_blank" rel="noopener noreferrer"' in html and "<iframe" not in html


def test_starter_on_lab_host_needs_no_session(lab_host, competition, coordinator, problem, participant):
    enable(competition)
    task = configured(problem, coordinator)
    path = lab_token_path(problem, participant)
    client = Client()  # bez logowania: host laboratorium nie dostaje ciasteczek serwisu
    response = on_lab(client, path)
    assert response.status_code == 200
    body = response.content.decode()
    assert "check(TESTS" in body and HIDDEN_SENTINEL not in body
    assert response["Cache-Control"] == "no-store"
    assert response["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'; sandbox"
    assert response["Cross-Origin-Resource-Policy"] == "same-origin"
    assert response["Content-Disposition"].startswith("inline")
    assert on_lab(client, f"{path}?download=1")["Content-Disposition"].startswith("attachment")
    assert not response.cookies
    token = path.split("/")[2]
    filename = services.starter_filename(task)
    assert on_lab(client, f"/notebook-starter/{token}/inny.ipynb").status_code == 404
    assert on_lab(client, f"/notebook-starter/{token}x/{filename}").status_code == 404
    assert client.post(path, HTTP_HOST=LAB).status_code == 405


def test_starter_tokens_of_the_two_modes_do_not_mix(
    settings, lab_host, competition, coordinator, problem, participant
):
    enable(competition)
    task = configured(problem, coordinator)
    filename = services.starter_filename(task)
    data = {"c": competition.pk, "t": task.pk, "u": participant.user.pk}
    same_origin_token = signing.dumps(data, salt=services.STARTER_SALT, compress=True)
    assert on_lab(Client(), f"/notebook-starter/{same_origin_token}/{filename}").status_code == 404
    # I odwrotnie: token hosta laboratorium nie działa w trybie QC-01 (inna sól).
    lab_token = signing.dumps(data, salt=services.LAB_STARTER_SALT, compress=True)
    settings.NOTEBOOK_LAB_HOST = ""
    client = Client()
    client.force_login(participant.user)
    assert client.get(f"/notebook-starter/{lab_token}/{filename}").status_code == 404


def test_expired_lab_token_is_refused(monkeypatch, lab_host, competition, coordinator, problem, participant):
    enable(competition)
    configured(problem, coordinator)
    path = lab_token_path(problem, participant)
    assert services.LAB_STARTER_MAX_AGE <= 2 * 3600
    monkeypatch.setattr(services, "LAB_STARTER_MAX_AGE", -1)
    assert on_lab(Client(), path).status_code == 404


@pytest.mark.parametrize("change", ["inactive", "staff", "coordinator_role", "no_entry", "flag_off"])
def test_lab_token_checks_current_state_of_the_account(
    lab_host, competition, coordinator, problem, participant, change
):
    enable(competition)
    configured(problem, coordinator)
    path = lab_token_path(problem, participant)
    assert on_lab(Client(), path).status_code == 200
    user = participant.user
    if change == "inactive":
        user.is_active = False
        user.save(update_fields=["is_active"])
    elif change == "staff":
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    elif change == "coordinator_role":
        grant_membership(user, competition, CompetitionRole.COORDINATOR)
    elif change == "no_entry":
        from apps.competitions.models import StageEntry

        StageEntry.objects.filter(participant=participant).delete()
    else:
        competition.feature_flags = {**competition.feature_flags, "quantum_notebooks": False}
        competition.save(update_fields=["feature_flags"])
    assert on_lab(Client(), path).status_code == 404


def test_lab_token_respects_proctoring_gate(lab_host, competition, coordinator, problem, participant):
    from apps.proctoring.models import ProctoringConfig

    enable(competition)
    configured(problem, coordinator)
    path = lab_token_path(problem, participant)
    competition.feature_flags = {**competition.feature_flags, "proctoring": True}
    competition.save(update_fields=["feature_flags"])
    ProctoringConfig.objects.create(stage=problem.stage, enabled=True)
    assert on_lab(Client(), path).status_code == 403


# --- strażnik żądań z laboratorium na hostach serwisu ---------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "headers", "refused"),
    [
        ("post", "/me/", {"HTTP_ORIGIN": f"https://{LAB}"}, True),
        ("post", "/me/", {"HTTP_ORIGIN": f"http://{LAB}"}, True),
        ("post", "/me/", {"HTTP_REFERER": f"https://{LAB}/"}, True),
        (
            "get",
            "/api/competitions/",
            {"HTTP_REFERER": f"https://{LAB}/", "HTTP_SEC_FETCH_MODE": "navigate"},
            True,
        ),
        ("get", "/me/", {"HTTP_REFERER": f"https://{LAB}/", "HTTP_SEC_FETCH_MODE": "cors"}, True),
        ("get", "/me/", {"HTTP_REFERER": f"https://{LAB}/", "HTTP_SEC_FETCH_MODE": "no-cors"}, True),
        ("get", "/me/", {"HTTP_ORIGIN": f"https://{LAB}", "HTTP_SEC_FETCH_MODE": "cors"}, True),
        # Zwykła nawigacja z laboratorium (odnośnik w notatniku) – jak z każdej obcej strony.
        ("get", "/me/", {"HTTP_REFERER": f"https://{LAB}/", "HTTP_SEC_FETCH_MODE": "navigate"}, False),
        ("get", "/me/", {"HTTP_REFERER": f"https://{LAB}/"}, False),
        # Żądania spoza laboratorium – strażnik się nie wtrąca.
        ("post", "/me/", {"HTTP_ORIGIN": "http://testserver"}, False),
        ("get", "/me/", {"HTTP_REFERER": "https://inny.example/", "HTTP_SEC_FETCH_MODE": "no-cors"}, False),
    ],
)
def test_platform_refuses_requests_from_the_lab_host(lab_host, participant, method, path, headers, refused):
    client = Client()
    client.force_login(participant.user)
    response = getattr(client, method)(path, **headers)
    assert (response.content == FORBIDDEN_TEXT.encode()) is refused
    if refused:
        assert response.status_code == 403


def test_lab_origin_refused_even_when_csrf_would_trust_it(settings, lab_host, participant):
    """Subdomena platformy: ``https://*.<domena>`` w ``CSRF_TRUSTED_ORIGINS`` objąłby laboratorium,
    a podrzucone ciasteczko ``csrftoken`` (cookie tossing, QC-02 § 5) dałoby ważny token. Strażnik
    odrzuca żądanie przed ``CsrfViewMiddleware``."""
    settings.CSRF_TRUSTED_ORIGINS = [*settings.CSRF_TRUSTED_ORIGINS, "http://*.example.test"]
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant.user)
    token = "a" * 32
    client.cookies[settings.CSRF_COOKIE_NAME] = token  # „podrzucone” ciasteczko
    data = {"csrfmiddlewaretoken": token}
    response = client.post("/me/", data, HTTP_ORIGIN=f"http://{LAB}")
    assert response.status_code == 403 and response.content == FORBIDDEN_TEXT.encode()
    # Kontrola: bez strażnika to samo żądanie przeszłoby ochronę CSRF Django (zaufany origin, token
    # zgodny z ciasteczkiem) – czyli strażnik jest tu jedyną zaporą po stronie serwera.
    settings.NOTEBOOK_LAB_HOST = ""
    response = client.post("/account/preferences/", data, HTTP_ORIGIN=f"http://{LAB}")
    assert response.status_code == 302  # zapis preferencji przeszedł – CSRF Django go nie zatrzymał
    settings.NOTEBOOK_LAB_HOST = LAB
    response = client.post("/account/preferences/", data, HTTP_ORIGIN=f"http://{LAB}")
    assert response.status_code == 403 and response.content == FORBIDDEN_TEXT.encode()


def test_coordinator_warning_names_the_lab_host(lab_host, competition, coordinator):
    enable(competition)
    client = Client()
    client.force_login(coordinator)
    html = client.get("/coordinator/notebooks/").content.decode()
    assert f"laboratorium działa pod osobnym adresem {LAB}" in html and "§ 40.7" in html
    assert "laboratorium działa w domenie serwisu" not in html


# --- ustawienia, sprawdzenie systemowe, ciasteczka -------------------------------------------------


def test_settings_allow_lab_host_but_never_trust_it_for_csrf(monkeypatch):
    monkeypatch.setenv("NOTEBOOK_LAB_HOST", " Lab.Example.org. ")
    monkeypatch.setenv("SITE_DOMAIN", "example.org")
    monkeypatch.setenv("PLATFORM_SUBDOMAINS", "1")
    monkeypatch.setenv("DJANGO_CSRF_TRUSTED_ORIGINS", "")
    base = Path(django_settings.BASE_DIR) / "config" / "settings" / "base.py"
    values = runpy.run_path(str(base))
    assert values["NOTEBOOK_LAB_HOST"] == "lab.example.org"
    assert "lab.example.org" in values["ALLOWED_HOSTS"]
    assert not any("lab." in origin for origin in values["CSRF_TRUSTED_ORIGINS"])
    # Wzorzec subdomen platformy obejmuje host laboratorium – dlatego jest strażnik (QC-02 § 4).
    assert "https://*.example.org" in values["CSRF_TRUSTED_ORIGINS"]
    assert values["MIDDLEWARE"].index("apps.notebooks.middleware.NotebookLabHostMiddleware") < values[
        "MIDDLEWARE"
    ].index("whitenoise.middleware.WhiteNoiseMiddleware")
    assert values["MIDDLEWARE"].index("apps.notebooks.middleware.NotebookLabRequestGuardMiddleware") < values[
        "MIDDLEWARE"
    ].index("django.middleware.csrf.CsrfViewMiddleware")


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("", True),
        ("lab.example.org", True),
        ("olimpiada-lab.pl", True),
        ("lab.localhost:8000", True),
        ("https://lab.example.org", False),
        ("lab.example.org/x", False),
        ("lab", False),
        ("example.org", False),  # SITE_DOMAIN
        ("konkurs.example", False),  # EXTRA_DOMAINS
        ("www.konkurs.example", False),
    ],
)
def test_lab_host_check(settings, value, ok):
    settings.SITE_DOMAIN = "example.org"
    settings.EXTRA_DOMAINS = ["konkurs.example"]
    settings.NOTEBOOK_LAB_HOST = value
    errors = lab_host_is_separate(None)
    assert (errors == []) is ok
    if not ok:
        assert errors[0].id == "notebooks.E002"


def test_platform_cookies_are_host_only(settings, participant):
    """Host laboratorium (także ``lab.<domena>``) nie dostaje ciasteczek serwisu (QC-02 § 5)."""
    assert settings.SESSION_COOKIE_DOMAIN is None
    assert settings.CSRF_COOKIE_DOMAIN is None
    assert settings.LANGUAGE_COOKIE_DOMAIN is None
    client = Client()
    client.force_login(participant.user)
    response = client.get("/me/")
    assert response.cookies, "strona panelu ustawia csrftoken"
    response = client.post("/account/preferences/", {"language": "en"})
    for morsel in [*response.cookies.values(), *client.cookies.values()]:
        assert morsel["domain"] == "", morsel.key


# --- przegląd M1: podrzucone ciasteczka (cookie tossing z lab.<domena>) ----------------------------

MAIN = "example.test"


@pytest.fixture
def main_host(settings, lab_host):
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, MAIN]
    return MAIN


def set_cookie_headers(response) -> list[str]:
    return [value for name, value in response.items() if name == "Set-Cookie"]


@pytest.mark.parametrize(("method", "status"), [("get", 302), ("post", 307)])
def test_tossed_duplicate_cookies_are_wiped_on_parent_domain(main_host, method, status):
    raw = "csrftoken=legit; sessionid=s1; csrftoken=tossed; django_language=pl"
    response = getattr(Client(), method)("/me/notebooks/5/?a=1", HTTP_HOST=MAIN, HTTP_COOKIE=raw)
    assert isinstance(response, cookieguard.CookieWipeRedirect)
    assert response.status_code == status and response["Location"] == "/me/notebooks/5/?a=1"
    headers = set_cookie_headers(response)
    for path in (
        "/",
        "/me",
        "/me/",
        "/me/notebooks",
        "/me/notebooks/",
        "/me/notebooks/5",
        "/me/notebooks/5/",
    ):
        expected = f"csrftoken=; Domain=example.test; Path={path}; Max-Age=0; Expires={cookieguard.EXPIRED}"
        assert f"{expected}; SameSite=Lax" in headers, path
    # Tylko zdublowane nazwy; host-only ciasteczka serwisu (bez ``Domain``) nie są wygaszane.
    assert not any(h.startswith(("sessionid=", "django_language=")) for h in headers)
    assert all("Domain=" in h for h in headers)
    assert response.cookies[cookieguard.MARKER].value == "1"


def test_duplicates_after_failed_wipe_are_dropped_not_trusted(main_host, participant):
    client = Client()
    client.force_login(participant.user)
    sid = client.cookies[django_settings.SESSION_COOKIE_NAME].value
    raw = f"sessionid={sid}; sessionid=podrzucona; {cookieguard.MARKER}=1"
    response = client.get("/me/", HTTP_HOST=MAIN, HTTP_COOKIE=raw)
    assert not isinstance(response, cookieguard.CookieWipeRedirect)  # bez pętli przekierowań
    assert "sessionid" not in response.wsgi_request.COOKIES
    assert not response.wsgi_request.user.is_authenticated  # żadna z dwóch sesji nie jest wiarygodna


def test_no_wipe_without_duplicates_or_with_separate_domain(settings, main_host):
    client = Client()
    response = client.get("/me/", HTTP_HOST=MAIN, HTTP_COOKIE="csrftoken=a; sessionid=b")
    assert not isinstance(response, cookieguard.CookieWipeRedirect)
    settings.NOTEBOOK_LAB_HOST = "olimpiada-lab.test"  # wariant B: inny „site”, podrzucenie niemożliwe
    response = client.get("/me/", HTTP_HOST=MAIN, HTTP_COOKIE="csrftoken=a; csrftoken=b")
    assert not isinstance(response, cookieguard.CookieWipeRedirect)
    assert cookieguard.shared_parent_domains(MAIN, "olimpiada-lab.test") == []
    for host in ("konkurs.olimpiadakwantowa.pl", "olimpiadakwantowa.pl"):
        assert cookieguard.shared_parent_domains(host, "lab.olimpiadakwantowa.pl") == ["olimpiadakwantowa.pl"]


def test_production_cookie_names_get_host_prefix_only_with_lab_host_and_tls():
    from config.settings.base import host_prefixed_cookie_name

    assert host_prefixed_cookie_name("sessionid", lab_host=LAB, secure=True) == "__Host-sessionid"
    assert host_prefixed_cookie_name("csrftoken", lab_host=LAB, secure=True) == "__Host-csrftoken"
    assert host_prefixed_cookie_name("sessionid", lab_host="", secure=True) == "sessionid"
    # ``__Host-`` bez ``Secure`` przeglądarka odrzuca – dev bez TLS zostaje przy zwykłej nazwie.
    assert host_prefixed_cookie_name("sessionid", lab_host=LAB, secure=False) == "sessionid"
    source = (Path(django_settings.BASE_DIR) / "config" / "settings" / "production.py").read_text(
        encoding="utf-8"
    )
    compact = "".join(source.split())
    assert 'SESSION_COOKIE_NAME=host_prefixed_cookie_name("sessionid",lab_host=NOTEBOOK_LAB_HOST' in compact
    assert 'CSRF_COOKIE_NAME=host_prefixed_cookie_name("csrftoken",lab_host=NOTEBOOK_LAB_HOST' in compact


def test_host_prefixed_cookie_names_work_end_to_end(settings, main_host, participant):
    """Z prefiksem ``__Host-`` (produkcja z laboratorium) sesja i CSRF działają, a podrzucone
    ciasteczka o starych nazwach nie mają znaczenia."""
    settings.SESSION_COOKIE_NAME = "__Host-sessionid"
    settings.CSRF_COOKIE_NAME = "__Host-csrftoken"
    client = Client(enforce_csrf_checks=True)
    client.force_login(participant.user)
    assert "__Host-sessionid" in client.cookies
    client.cookies["sessionid"] = "podrzucona"
    client.cookies["csrftoken"] = "b" * 32
    response = client.get("/me/")
    assert response.wsgi_request.user == participant.user
    token = client.cookies["__Host-csrftoken"].value
    response = client.post("/account/preferences/", {"language": "en", "csrfmiddlewaretoken": token})
    assert response.status_code == 302


# --- przegląd L1: żądania same-site bez przypisania ----------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "headers", "refused"),
    [
        ("post", "/me/", {"HTTP_ORIGIN": "null", "HTTP_SEC_FETCH_SITE": "same-site"}, True),
        ("post", "/me/", {"HTTP_SEC_FETCH_SITE": "same-site", "HTTP_SEC_FETCH_MODE": "cors"}, True),
        (
            "get",
            "/api/competitions/",
            {"HTTP_SEC_FETCH_SITE": "same-site", "HTTP_SEC_FETCH_MODE": "cors"},
            True,
        ),
        # Cross-site ``Origin: null`` (np. powrót od operatora płatności) – nie ta reguła.
        ("post", "/me/", {"HTTP_ORIGIN": "null", "HTTP_SEC_FETCH_SITE": "cross-site"}, False),
        # GET zasobu same-site bez nagłówków (np. z subdomeny konkursu) – przechodzi.
        ("get", "/me/", {"HTTP_SEC_FETCH_SITE": "same-site", "HTTP_SEC_FETCH_MODE": "no-cors"}, False),
        # Same-site z originem innym niż laboratorium (subdomena konkursu) – przechodzi.
        (
            "post",
            "/me/",
            {"HTTP_ORIGIN": "https://fizyka.example.test", "HTTP_SEC_FETCH_SITE": "same-site"},
            False,
        ),
    ],
)
def test_anonymous_same_site_requests_are_refused(main_host, method, path, headers, refused):
    response = getattr(Client(), method)(path, HTTP_HOST=MAIN, **headers)
    assert (response.content == FORBIDDEN_TEXT.encode()) is refused


def test_anonymous_same_site_rule_only_for_same_site_lab(settings, main_host):
    settings.NOTEBOOK_LAB_HOST = "olimpiada-lab.test"
    response = Client().post("/me/", HTTP_HOST=MAIN, HTTP_ORIGIN="null", HTTP_SEC_FETCH_SITE="same-site")
    assert response.content != FORBIDDEN_TEXT.encode()


# --- przegląd L2: host laboratorium nie może być hostem usługi ani konkursu -------------------------


@pytest.mark.parametrize(
    "value",
    ["dj.example.org", "live.example.org", "meet.example.org", "monitor.example.org", "errors.example.org",
     "s3.example.org", "media.example.org", "jitsi.example.org"],
)  # fmt: skip
def test_lab_host_check_refuses_platform_service_hosts(settings, value):
    settings.SITE_DOMAIN = "example.org"
    settings.EXTRA_DOMAINS = []
    settings.S3_PUBLIC_ENDPOINT_URL = "https://media.example.org"
    settings.JITSI_JWT_HOST = "jitsi.example.org"
    settings.NOTEBOOK_LAB_HOST = value
    assert [error.id for error in lab_host_is_separate(None)] == ["notebooks.E002"]


def test_lab_host_check_refuses_competition_host(settings, competition):
    from apps.notebooks.checks import lab_host_is_not_a_competition
    from apps.tenancy.models import Competition

    settings.NOTEBOOK_LAB_HOST = "lab.example.org"
    assert lab_host_is_not_a_competition(None, databases=["default"]) == []
    Competition.objects.filter(pk=competition.pk).update(primary_domain="lab.example.org")
    errors = lab_host_is_not_a_competition(None, databases=["default"])
    assert [error.id for error in errors] == ["notebooks.E002"]
    assert lab_host_is_not_a_competition(None, databases=None) == []  # bez bazy – nic

"""Strażnik laboratorium notatników w djcms (QC-02, przegląd M2 i L1): żądania z hosta laboratorium,
żądania same-site bez przypisania i podrzucone ciasteczka ``djcms_*``."""

import pytest

from apps.pages.labguard import FORBIDDEN_TEXT, MARKER, CookieWipeRedirect, shared_parent_domains

pytestmark = pytest.mark.django_db

HOST = "fizyka.olimpiada.example"
LAB = "lab.olimpiada.example"


@pytest.fixture
def lab_on(settings, make_competition):
    make_competition("fizyka", hosts=[HOST])
    settings.NOTEBOOK_LAB_HOST = LAB
    settings.CSRF_TRUSTED_ORIGINS = [*settings.CSRF_TRUSTED_ORIGINS, "https://*.olimpiada.example"]
    return LAB


def wipes(response) -> list[str]:
    return [value for name, value in response.items() if name == "Set-Cookie"]


def test_settings_read_lab_host_and_place_guard_before_csrf(settings):
    order = settings.MIDDLEWARE
    assert order.index("apps.pages.labguard.LabGuardMiddleware") < order.index(
        "django.contrib.sessions.middleware.SessionMiddleware"
    )
    assert order.index("apps.pages.labguard.LabGuardMiddleware") < order.index(
        "django.middleware.csrf.CsrfViewMiddleware"
    )
    assert settings.NOTEBOOK_LAB_HOST == ""


@pytest.mark.parametrize(
    ("method", "headers", "refused"),
    [
        ("post", {"HTTP_ORIGIN": f"https://{LAB}"}, True),
        ("post", {"HTTP_REFERER": f"https://{LAB}/"}, True),
        ("get", {"HTTP_REFERER": f"https://{LAB}/", "HTTP_SEC_FETCH_MODE": "cors"}, True),
        ("get", {"HTTP_REFERER": f"https://{LAB}/", "HTTP_SEC_FETCH_MODE": "navigate"}, False),
        # L1: Origin wycięty przez kod z notatnika (``no-referrer``), żądanie same-site.
        ("post", {"HTTP_ORIGIN": "null", "HTTP_SEC_FETCH_SITE": "same-site"}, True),
        ("post", {"HTTP_SEC_FETCH_SITE": "same-site", "HTTP_SEC_FETCH_MODE": "cors"}, True),
        ("post", {"HTTP_ORIGIN": "null", "HTTP_SEC_FETCH_SITE": "cross-site"}, False),
        ("get", {"HTTP_SEC_FETCH_SITE": "same-site", "HTTP_SEC_FETCH_MODE": "no-cors"}, False),
        ("post", {"HTTP_ORIGIN": f"https://{HOST}"}, False),
    ],
)
def test_requests_from_the_lab_are_refused(client, lab_on, method, headers, refused):
    response = getattr(client, method)("/", HTTP_HOST=HOST, **headers)
    assert (response.content == FORBIDDEN_TEXT.encode()) is refused
    if refused:
        assert response.status_code == 403


def test_tossed_csrf_token_with_trusted_origin_is_refused(client, lab_on):
    """Bez strażnika: wzorzec ``https://*.olimpiada.example`` + podrzucony ``djcms_csrftoken`` = zapis."""
    token = "a" * 32
    response = client.post(
        "/",
        {"csrfmiddlewaretoken": token},
        HTTP_HOST=HOST,
        HTTP_ORIGIN=f"https://{LAB}",
        HTTP_COOKIE=f"djcms_csrftoken={token}",
    )
    assert response.status_code == 403 and response.content == FORBIDDEN_TEXT.encode()


def test_tossed_csrf_token_passes_django_csrf_without_the_guard(settings, lab_on):
    """Kontrola tezy M2: bez strażnika ochrona CSRF Django **przepuszcza** takie żądanie."""
    from django.test import Client

    settings.NOTEBOOK_LAB_HOST = ""
    token = "a" * 32
    response = Client(enforce_csrf_checks=True).post(
        "/",
        {"csrfmiddlewaretoken": token},
        HTTP_HOST=HOST,
        HTTP_ORIGIN=f"https://{LAB}",
        HTTP_COOKIE=f"djcms_csrftoken={token}",
    )
    assert response.status_code != 403


def test_without_lab_host_guard_is_inert(client, settings, make_competition):
    make_competition("fizyka", hosts=[HOST])
    response = client.post("/", HTTP_HOST=HOST, HTTP_ORIGIN=f"https://{LAB}")
    assert response.content != FORBIDDEN_TEXT.encode()
    response = client.get("/", HTTP_HOST=HOST, HTTP_COOKIE="djcms_csrftoken=a; djcms_csrftoken=b")
    assert not isinstance(response, CookieWipeRedirect)


@pytest.mark.parametrize(("method", "status"), [("get", 302), ("post", 307)])
def test_duplicated_cookies_are_wiped_on_parent_domain(client, lab_on, method, status):
    raw = "djcms_csrftoken=legit; djcms_sessionid=s1; djcms_csrftoken=tossed; djcms_sessionid=s2"
    response = getattr(client, method)("/a/b/?x=1", HTTP_HOST=HOST, HTTP_COOKIE=raw)
    assert response.status_code == status
    assert response["Location"] == "/a/b/?x=1"
    headers = wipes(response)
    for name in ("djcms_csrftoken", "djcms_sessionid"):
        for path in ("/", "/a", "/a/", "/a/b", "/a/b/"):
            assert any(
                h.startswith(f"{name}=; Domain=olimpiada.example; Path={path}; Max-Age=0") for h in headers
            ), (name, path)
    assert not any("Domain=example;" in h or "Domain=fizyka." in h for h in headers)
    assert response.cookies[MARKER].value == "1"


def test_duplicates_after_wipe_are_dropped_not_trusted(client, lab_on):
    raw = f"djcms_sessionid=s1; djcms_sessionid=s2; {MARKER}=1"
    response = client.get("/", HTTP_HOST=HOST, HTTP_COOKIE=raw)
    # Drugie podejście (znacznik żyje): bez pętli przekierowań – żądanie idzie dalej bez obu kopii.
    assert not isinstance(response, CookieWipeRedirect)
    assert response.wsgi_request.COOKIES.get("djcms_sessionid") is None
    assert response.wsgi_request.COOKIES.get(MARKER) == "1"


def test_separate_registrable_domain_never_wipes(client, settings, make_competition):
    make_competition("fizyka", hosts=[HOST])
    settings.NOTEBOOK_LAB_HOST = "olimpiada-lab.example"
    response = client.get("/", HTTP_HOST=HOST, HTTP_COOKIE="djcms_csrftoken=a; djcms_csrftoken=b")
    assert not isinstance(response, CookieWipeRedirect)
    assert shared_parent_domains(HOST, "olimpiada-lab.example") == []
    assert shared_parent_domains(HOST, LAB) == ["olimpiada.example"]

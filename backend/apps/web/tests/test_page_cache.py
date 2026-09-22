"""Cache całych stron publicznych (``apps/web/page_cache.py``).

Trzy warstwy testów, bo trzy różne rzeczy naprawdę zależą od siebie inaczej:

- **żądanie HTTP przez ``client_for``** – dla zachowań, które ma zobaczyć przeglądarka: nagłówek
  ``X-Page-Cache``, nonce CSP zgodny z nagłówkiem na trafieniu, obejścia (zalogowany, POST, adres
  spoza allow-listy, parametr zapytania inny niż ``?page=``), wyłącznik ustawienia.
- **bezpośrednie wywołanie middleware'u z podstawionym ``get_response``** – tam, gdzie prawdziwe
  drzewo stron nie jest potrzebne (izolacja dwóch konkursów – wystarczą dwa różne ciała odpowiedzi)
  albo przeszkadzałoby (koszt zapytań trafienia musi liczyć **wyłącznie** tę warstwę, bez kosztu
  rozstrzygania konkursu, który i tak biegnie wyżej w łańcuchu niezależnie od cache'a).
- **wywołania funkcji modułu wprost** – dla furtek bezpieczeństwa, których nie da się wywołać
  prawdziwym żądaniem gościa (``Set-Cookie`` ustawiony przez widok, sesja zmieniona w trakcie
  obsługi, dwa wystąpienia tokenu CSRF w treści).
"""

from __future__ import annotations

import re

import pytest
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from apps.accounts.tests.factories import UserFactory
from apps.web import page_cache

pytestmark = pytest.mark.django_db

NONCE_RE = re.compile(r'nonce="([^"]+)"')
CSP_NONCE_RE = re.compile(r"nonce-([A-Za-z0-9_=-]+)")
CSRF_RE = re.compile(r'"X-CSRFToken":\s*"([^"]+)"')


def _script_nonces(content: str) -> set[str]:
    return set(NONCE_RE.findall(content))


def _enable(settings, *, seconds: int | None = None) -> None:
    settings.PAGE_CACHE_ENABLED = True
    if seconds is not None:
        settings.PAGE_CACHE_SECONDS = seconds


# --- Trafienie/chybienie przez prawdziwe żądanie --------------------------------------------------


def test_miss_then_hit_same_body_except_nonce_and_csrf_token(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)

    first = client.get("/")
    second = client.get("/")

    assert first["X-Page-Cache"] == "MISS"
    assert second["X-Page-Cache"] == "HIT"

    first_body = first.content.decode()
    second_body = second.content.decode()

    first_nonces = _script_nonces(first_body)
    second_nonces = _script_nonces(second_body)
    # Jeden nonce na odpowiedź (patrz test niżej), a oba żądania mają dostać **własny**.
    assert len(first_nonces) == 1
    assert len(second_nonces) == 1
    assert first_nonces != second_nonces

    first_csrf = CSRF_RE.search(first_body).group(1)
    second_csrf = CSRF_RE.search(second_body).group(1)
    assert first_csrf != second_csrf  # zamaskowany na nowo przy każdym wywołaniu get_token()

    # Po podstawieniu nonce'u i tokenu z powrotem na placeholder oba ciała są identyczne – to jest
    # dowód, że trafienie serwuje **tę samą** wyrenderowaną treść, a nie osobno wyliczoną stronę.
    normalised_first = first_body.replace(next(iter(first_nonces)), "@nonce@").replace(first_csrf, "@csrf@")
    normalised_second = second_body.replace(next(iter(second_nonces)), "@nonce@").replace(
        second_csrf, "@csrf@"
    )
    assert normalised_first == normalised_second


def test_hit_nonce_matches_csp_header(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)
    client.get("/")  # rozgrzanie: pierwsze wejście zapisuje wpis w cache'u

    hit = client.get("/")
    assert hit["X-Page-Cache"] == "HIT"

    header_nonce = CSP_NONCE_RE.search(hit["Content-Security-Policy"]).group(1)
    body_nonces = _script_nonces(hit.content.decode())
    assert body_nonces == {header_nonce}


def test_authenticated_visitor_always_bypasses(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)
    client.force_login(UserFactory())

    first = client.get("/")
    second = client.get("/")

    assert first["X-Page-Cache"] == "BYPASS"
    assert second["X-Page-Cache"] == "BYPASS"


def test_post_bypasses_cache(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)

    response = client.post("/")

    assert response["X-Page-Cache"] == "BYPASS"


def test_path_outside_allow_list_bypasses_cache(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)

    response = client.get("/coordinator/")

    assert response["X-Page-Cache"] == "BYPASS"


def test_unknown_query_string_bypasses_cache(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)

    response = client.get("/", {"utm_source": "newsletter"})

    assert response["X-Page-Cache"] == "BYPASS"


def test_page_query_parameter_is_cached(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)

    first = client.get("/", {"page": "2"})
    second = client.get("/", {"page": "2"})

    assert first["X-Page-Cache"] == "MISS"
    assert second["X-Page-Cache"] == "HIT"


def test_disabled_by_setting_always_bypasses(client_for, competition, settings):
    # Domyślne ustawienie testów (``config/settings/test.py``) – nie włączamy niczego.
    assert settings.PAGE_CACHE_ENABLED is False
    client = client_for(competition)

    first = client.get("/")
    second = client.get("/")

    assert first["X-Page-Cache"] == "BYPASS"
    assert second["X-Page-Cache"] == "BYPASS"


# --- Bezpieczne furtki: wywołania funkcji modułu wprost --------------------------------------------


def test_storable_rejects_response_with_set_cookie():
    request = RequestFactory().get("/")
    response = HttpResponse("<html></html>", content_type="text/html")
    response.set_cookie("something", "value")

    assert page_cache._storable(request, response) is False


def test_storable_rejects_response_when_session_was_modified():
    from django.contrib.sessions.backends.db import SessionStore

    request = RequestFactory().get("/")
    request.session = SessionStore()
    request.session["anything"] = "value"  # dowolny zapis oznacza sesję jako zmienioną
    response = HttpResponse("<html></html>", content_type="text/html")

    assert page_cache._storable(request, response) is False


def test_storable_rejects_non_200_and_non_html():
    request = RequestFactory().get("/")

    redirect = HttpResponse(status=302)
    assert page_cache._storable(request, redirect) is False

    json_response = HttpResponse("{}", content_type="application/json")
    assert page_cache._storable(request, json_response) is False


def test_placeholder_body_refuses_ambiguous_csrf_token():
    request = RequestFactory().get("/")
    request.csp_nonce = "abc123"
    # Wzorzec jest wąski (patrz docstring ``_CSRF_TOKEN_RE``): łapie wyłącznie parę klucz/wartość
    # ``"X-CSRFToken": "…"``, więc żeby dostać dwa dopasowania, potrzeba dwóch takich par.
    body = (
        b'<body hx-headers=\'{"X-CSRFToken": "tok1"}\'>'
        b'<div hx-headers=\'{"X-CSRFToken": "tok2"}\'></div></body>'
    )
    assert page_cache._placeholder_body(request, body) is None


def test_placeholder_body_and_materialize_roundtrip():
    request = RequestFactory().get("/")
    request.csp_nonce = "the-nonce"
    body = b'<script nonce="the-nonce"></script><body hx-headers=\'{"X-CSRFToken": "the-token"}\'>'

    stored = page_cache._placeholder_body(request, body)
    assert stored is not None
    assert b"the-nonce" not in stored
    assert b"the-token" not in stored

    request.csp_nonce = "fresh-nonce"
    materialised = page_cache._materialize_body(request, stored)
    assert b"fresh-nonce" in materialised
    assert b"the-nonce" not in materialised
    # Token świeży pochodzi z ``get_token(request)`` – to nie jest ten sam string co wcześniej,
    # ale placeholder na pewno zniknął.
    assert page_cache.CSRF_PLACEHOLDER not in materialised


def test_query_suffix_allows_only_page_parameter():
    factory = RequestFactory()

    assert page_cache._query_suffix(factory.get("/")) == ""
    assert page_cache._query_suffix(factory.get("/", {"page": "3"})) == "page=3"
    assert page_cache._query_suffix(factory.get("/", {"page": "abc"})) is None
    assert page_cache._query_suffix(factory.get("/", {"foo": "bar"})) is None
    assert page_cache._query_suffix(factory.get("/?page=1&foo=bar")) is None


def test_is_cacheable_path_allow_list():
    assert page_cache.is_cacheable_path("/")
    assert page_cache.is_cacheable_path("/wyniki/")
    assert page_cache.is_cacheable_path("/aktualnosci/jakis-tekst/")
    assert page_cache.is_cacheable_path("/dokumenty/regulamin/")
    assert not page_cache.is_cacheable_path("/coordinator/")
    assert not page_cache.is_cacheable_path("/me/")
    assert not page_cache.is_cacheable_path("/cms/")
    assert not page_cache.is_cacheable_path("/api/auth/login/")


# --- Zerowy koszt zapytań na trafieniu --------------------------------------------------------------


def _hit_request(competition):
    """Żądanie z wypełnionymi atrybutami, których normalnie dostarcza łańcuch warstw pośrednich.

    Koszt trafienia mierzymy **wyłącznie** dla tej warstwy: rozstrzyganie konkursu
    (``CompetitionMiddleware``) biegnie wyżej w łańcuchu i tak czy inaczej, niezależnie od tego,
    czy ta warstwa w ogóle istnieje – liczenie go tutaj mierzyłoby cudzy kod.
    """
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    request = RequestFactory().get("/")
    request.competition = competition
    request.user = AnonymousUser()
    request.session = SessionStore()
    request.LANGUAGE_CODE = "pl"
    request.csp_nonce = "test-nonce"
    return request


def test_hit_costs_zero_sql_queries(competition, settings):
    _enable(settings)
    middleware = page_cache.PageCacheMiddleware(get_response=lambda req: HttpResponse("nigdy"))

    warmup = _hit_request(competition)
    response = middleware(warmup)
    assert response["X-Page-Cache"] == "MISS"

    hit_request = _hit_request(competition)
    with CaptureQueriesContext(connection) as captured:
        response = middleware(hit_request)
    assert response["X-Page-Cache"] == "HIT"
    assert len(captured.captured_queries) == 0


# --- Izolacja dwóch konkursów -----------------------------------------------------------------------


def test_cache_is_isolated_between_competitions(competition, other_competition, settings):
    _enable(settings)
    bodies = {competition.pk: b"strona konkursu A", other_competition.pk: b"strona konkursu B"}

    def get_response(request):
        return HttpResponse(bodies[request.competition.pk], content_type="text/html")

    middleware = page_cache.PageCacheMiddleware(get_response=get_response)

    for comp in (competition, other_competition):
        first = middleware(_hit_request(comp))
        assert first["X-Page-Cache"] == "MISS"

    for comp in (competition, other_competition):
        second = middleware(_hit_request(comp))
        assert second["X-Page-Cache"] == "HIT"
        assert second.content == bodies[comp.pk]


# --- Unieważnianie -----------------------------------------------------------------------------------


def _site_version(competition) -> int:
    return page_cache._version(f"{page_cache.VERSION_PREFIX}:{competition.pk}")


def test_invalidate_competition_bumps_only_that_site(competition, other_competition):
    before_a = _site_version(competition)
    before_b = _site_version(other_competition)

    page_cache.invalidate_competition(competition.pk)

    assert _site_version(competition) == before_a + 1
    assert _site_version(other_competition) == before_b


def test_invalidate_all_bumps_global_version():
    before = page_cache._version(f"{page_cache.VERSION_PREFIX}:global")

    page_cache.invalidate_all()

    assert page_cache._version(f"{page_cache.VERSION_PREFIX}:global") == before + 1


def test_page_publish_invalidates_its_site(competition):
    from apps.cms.models import HomePage

    home = HomePage.objects.get(pk=competition.site.root_page_id)
    before = _site_version(competition)

    home.save_revision().publish()

    assert _site_version(competition) == before + 1


def test_site_settings_save_invalidates_its_site(competition):
    from apps.cms.models import SiteSettings

    row = SiteSettings.for_site(competition.site)
    before = _site_version(competition)

    row.save()

    assert _site_version(competition) == before + 1


def test_announcement_save_invalidates_its_competition(competition):
    from apps.cms.models import Announcement

    before = _site_version(competition)

    announcement = Announcement.objects.create(competition=competition, text="Przerwa techniczna.")

    assert _site_version(competition) == before + 1

    before = _site_version(competition)
    announcement.text = "Przerwa techniczna – już po."
    announcement.save()
    assert _site_version(competition) == before + 1


def test_announcement_without_competition_bumps_global_version(unbound_competition, other_competition):
    """``unbound_competition`` wyłącza autouse'owe związanie z Konkursem #1 (``backend/conftest.py``).

    Bez tego ``Announcement.save()`` sam wypełniłby ``competition`` konkursem „na teraz”
    (``apps.cms.models.Announcement.save`` – ta sama reguła, co przy ``Edition.save``), więc
    stworzony tu komunikat nigdy naprawdę nie byłby **globalny**. ``other_competition`` z tego
    samego powodu: przy **jednym** konkursie w instalacji „na teraz” domyślnie znaczy „ten jedyny”
    (``apps.accounts.services.default_competition``) – dopiero drugi konkurs czyni odpowiedź
    naprawdę nieznaną.
    """
    from apps.cms.models import Announcement

    before = page_cache._version(f"{page_cache.VERSION_PREFIX}:global")

    announcement = Announcement.objects.create(competition=None, text="Komunikat całej instalacji.")

    assert announcement.competition_id is None
    assert page_cache._version(f"{page_cache.VERSION_PREFIX}:global") == before + 1


def test_stage_save_invalidates_its_competition(competition, elim_stage):
    before = _site_version(competition)

    elim_stage.name = "Etap I – zmiana terminu"
    elim_stage.save()

    assert _site_version(competition) == before + 1

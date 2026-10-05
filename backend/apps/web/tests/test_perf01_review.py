"""Poprawki po przeglądzie PERF-01 (M1, M2, L1, L4) – cache stron publicznych.

- M1: unieważnienie także **po** zatwierdzeniu transakcji – gość renderujący w oknie między
  podbiciem wersji a ``COMMIT`` nie zostawia starej tabeli pod nowym kluczem,
- M2: parametry śledzące nie trafiają do treści zapisanej w cache'u (pole ``next``),
- L1: ogłoszenie medali i zmiana konkursu (przełączniki) czyszczą cache witryny,
- L4: wpis, który rozpakowuje się ponad limit albo jest uszkodzony, to chybienie, a nie odpowiedź.
"""

from __future__ import annotations

import zlib

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse
from django.test import RequestFactory

from apps.competitions.tests.factories import StageFactory
from apps.results.tests.factories import ResultsPublicationFactory
from apps.web import page_cache


def _enable(settings) -> None:
    settings.PAGE_CACHE_ENABLED = True
    settings.PAGE_CACHE_SECONDS = 120


def _request(competition, path="/results/1/"):
    request = RequestFactory().get(path)
    request.competition, request.user, request.session = competition, AnonymousUser(), SessionStore()
    request.LANGUAGE_CODE, request.csp_nonce = "pl", "n"
    return request


# --- M1 ---------------------------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_a_page_rendered_before_the_commit_does_not_survive_it(competition, settings):
    """Gość w oknie przed ``COMMIT`` widzi stare dane i zapisuje je pod kluczem po pierwszym podbiciu."""
    _enable(settings)
    stage = StageFactory(competition=competition)
    stale = page_cache.PageCacheMiddleware(get_response=lambda req: HttpResponse(b"stara tabela"))
    fresh = page_cache.PageCacheMiddleware(get_response=lambda req: HttpResponse(b"nowa tabela"))

    with transaction.atomic():
        ResultsPublicationFactory(stage=stage)  # post_save: pierwsze podbicie, jeszcze przed COMMIT
        assert stale(_request(competition))["X-Page-Cache"] == "MISS"  # zapis „starej” treści

    after_commit = fresh(_request(competition))

    assert after_commit["X-Page-Cache"] == "MISS"
    assert after_commit.content == b"nowa tabela"


@pytest.mark.django_db
def test_outside_a_transaction_the_bump_is_immediate(competition):
    before = page_cache._versions(competition.pk)

    page_cache.invalidate_competition_on_commit(competition.pk)

    assert page_cache._versions(competition.pk) != before


# --- M2 ---------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_tracking_values_never_reach_the_cached_page(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)
    # Warunek wstępny: strona naprawdę wstawia pełny adres żądania (pole ``next``) – inaczej test
    # niczego by nie dowodził.
    assert "probe777" in client.get("/", {"q": "probe777"}).content.decode()

    poisoned = client.get("/", {"utm_source": "EVIL123", "fbclid": "EVIL456"})
    plain = client.get("/")

    assert poisoned["X-Page-Cache"] == "MISS"
    assert plain["X-Page-Cache"] == "HIT"
    for response in (poisoned, plain):
        body = response.content.decode()
        assert "EVIL123" not in body
        assert "EVIL456" not in body


@pytest.mark.django_db
def test_stripping_keeps_page_and_drops_only_tracking():
    request = RequestFactory().get("/aktualnosci/?utm_source=x&page=2&gclid=y")

    page_cache._strip_tracking_parameters(request)

    assert request.META["QUERY_STRING"] == "page=2"
    assert request.GET.dict() == {"page": "2"}
    assert request.get_full_path() == "/aktualnosci/?page=2"


# --- L1 ---------------------------------------------------------------------------------------------


@pytest.mark.django_db
def test_announcing_medals_clears_the_competition_pages(competition):
    from apps.medals.models import MedalScheme

    scheme = MedalScheme.objects.create(stage=StageFactory(competition=competition))
    before = page_cache._versions(competition.pk)

    scheme.save()  # ``freeze``/``unfreeze`` kończą się właśnie zapisem schematu

    assert page_cache._versions(competition.pk) != before


@pytest.mark.django_db
def test_changing_a_competition_flag_clears_its_pages(competition):
    before = page_cache._versions(competition.pk)

    competition.feature_flags = {**(competition.feature_flags or {}), "medals": True}
    competition.save(update_fields=["feature_flags"])

    assert page_cache._versions(competition.pk) != before


# --- L4 ---------------------------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [zlib.compress(b"0" * (page_cache.DECOMPRESS_MAX_BYTES + 1), 9), b"to nie jest zlib"],
    ids=["bomb", "corrupt"],
)
def test_oversized_or_corrupt_entry_is_a_miss(competition, settings, body):
    _enable(settings)
    key = page_cache.build_key(_request(competition))
    marks = {"nonce": "@@a@@", "csrf": "@@b@@"}
    cache.set(key, {"body": body, "zlib": True, "marks": marks, "content_type": "text/html"}, 60)
    middleware = page_cache.PageCacheMiddleware(get_response=lambda req: HttpResponse(b"wyrenderowana"))

    response = middleware(_request(competition))

    assert response["X-Page-Cache"] == "MISS"
    assert response.content == b"wyrenderowana"

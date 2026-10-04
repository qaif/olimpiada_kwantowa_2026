"""Gorące adresy dnia zawodów – budżety zapytań i poprawki z testu obciążenia (PERF-01).

Każdy test odpowiada jednemu ustaleniu z ``docs/tasks/PERF-01.md`` (§ 4) i pilnuje, żeby poprawka
nie cofnęła się po cichu:

- odpytanie czatu bez zmian (co 15 s u każdego ucznia z otwartą rozmową) – stały, mały budżet,
- treść zadania w PDF (wszyscy naraz w chwili otwarcia etapu) – budżet i kawałki po 64 KiB,
- autozapis testu online przez widok – liczba zapytań nie rośnie z liczbą odpowiedzi,
- cache stron: znaczniki kampanii (``utm_*``) nie omijają cache'u, tabela wyników ``/results/<id>/``
  jest w cache'u (skompresowana) i znika z niego po wycofaniu publikacji,
- limit wysyłek liczony per konto, a nie per adres IP (sala za jednym NAT-em).
"""

from __future__ import annotations

import json

import pytest
from django.db import connection
from django.http import HttpResponse
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext

from apps.chat import services as chat
from apps.chat.tests.helpers import participant_of
from apps.competitions.models import StageEntryStatus
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.quiz import services as quiz_services
from apps.quiz.tests.factories import QuizFactory, choice_question
from apps.results.tests.factories import ResultsPublicationFactory
from apps.web import page_cache, throttle

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}

#: Sufity zmierzone po poprawkach PERF-01 (4.10.2026), mierzone na ciepło – drugie żądanie tego
#: samego klienta, po rozgrzaniu pamięci procesu (menu, ustawienia witryny). Przed poprawkami:
#: odpytanie czatu 13 zapytań (dwa odczyty profilu), PDF 6.
CHAT_POLL_BUDGET = 12
STATEMENT_BUDGET = 8


def _count(client, path, **extra) -> tuple[int, object]:
    client.get(path, **extra)
    with CaptureQueriesContext(connection) as captured:
        response = client.get(path, **extra)
    return len(captured.captured_queries), response


# --- czat -------------------------------------------------------------------------------------------


def test_unchanged_chat_poll_stays_within_budget(web_client, competition):
    ala = participant_of(competition)
    conversation = chat.write_to_organizer(
        user=ala.user, competition=competition, body="Pytanie"
    ).conversation
    web_client.force_login(ala.user)
    url = f"/me/messages/{conversation.pk}/?fragment=messages&v={chat.thread_version(conversation, ala)}"

    queries, response = _count(web_client, url, **HTMX)

    assert response.status_code == 204
    assert queries <= CHAT_POLL_BUDGET


# --- treść zadania ----------------------------------------------------------------------------------


def test_statement_pdf_budget_and_chunk_size(web_client, entry):
    from django.core.files.base import ContentFile

    problem = ProblemFactory(stage=entry.stage, number=7)
    problem.statement_pdf.save("t.pdf", ContentFile(b"%PDF-1.4\n" + b"0" * 200_000), save=True)
    web_client.force_login(entry.participant.user)

    queries, response = _count(web_client, f"/api/competitions/problems/{problem.pk}/statement/")

    assert response.status_code == 200
    assert response.block_size == 64 * 1024
    assert queries <= STATEMENT_BUDGET
    assert b"".join(response.streaming_content).startswith(b"%PDF-1.4")


# --- autozapis testu przez widok --------------------------------------------------------------------


def test_quiz_autosave_view_cost_does_not_grow_with_answers(web_client, participant):
    quiz = QuizFactory()
    questions = [choice_question(quiz) for _ in range(10)]
    entry = StageEntryFactory(participant=participant, stage=quiz.stage, status=StageEntryStatus.REGISTERED)
    attempt = quiz_services.start_attempt(quiz=quiz, entry=entry)
    web_client.force_login(participant.user)
    url = f"/me/test/{attempt.pk}/zapis/"

    def post(items):
        payload = {str(q.pk): {"options": [q.options.order_by("order", "id")[1].pk]} for q in items}
        with CaptureQueriesContext(connection) as captured:
            response = web_client.post(url, json.dumps({"answers": payload}), content_type="application/json")
        assert response.status_code == 200, response.content
        return len(captured.captured_queries)

    post(questions[:1])  # rozgrzanie (sesja, menu)
    two = post(questions[:2])
    ten = post(questions)

    assert ten == two


# --- cache stron ------------------------------------------------------------------------------------


def _enable(settings) -> None:
    settings.PAGE_CACHE_ENABLED = True
    settings.PAGE_CACHE_SECONDS = 120


def test_campaign_parameters_share_the_plain_entry(client_for, competition, settings):
    _enable(settings)
    client = client_for(competition)

    first = client.get("/", {"utm_source": "newsletter", "utm_medium": "email"})
    second = client.get("/")
    third = client.get("/", {"fbclid": "abc"})

    assert first["X-Page-Cache"] == "MISS"
    assert second["X-Page-Cache"] == "HIT"
    assert third["X-Page-Cache"] == "HIT"


def test_query_suffix_ignores_only_tracking_parameters():
    factory = RequestFactory()

    assert page_cache._query_suffix(factory.get("/?utm_source=x&UTM_Campaign=y")) == ""
    assert page_cache._query_suffix(factory.get("/?page=2&utm_source=x")) == "page=2"
    assert page_cache._query_suffix(factory.get("/?gclid=1&page=3")) == "page=3"
    # Nieznany parametr dalej wyłącza cache – także w towarzystwie znaczników kampanii.
    assert page_cache._query_suffix(factory.get("/?utm_source=x&q=1")) is None
    assert page_cache._query_suffix(factory.get("/?page=2&page=3")) is None
    assert page_cache._query_suffix(factory.get("/?utmost=1")) is None


def test_results_table_is_cached_and_dropped_when_withdrawn(client_for, competition, elim_stage, settings):
    _enable(settings)
    publication = ResultsPublicationFactory(
        stage=elim_stage,
        snapshot=[
            {"rank": n, "display": f"OK{n:06d}", "points": {"1": "5"}, "total": "5", "qualified": True}
            for n in range(1, 4)
        ],
    )
    client = client_for(competition)
    url = f"/results/{elim_stage.pk}/"

    assert client.get(url)["X-Page-Cache"] == "MISS"
    assert client.get(url)["X-Page-Cache"] == "HIT"

    publication.delete()

    assert client.get(url).status_code == 404


def test_large_page_is_stored_compressed_and_served_byte_for_byte(competition, settings):
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore
    from django.core.cache import cache

    _enable(settings)
    body = ("<tr><td>wiersz tabeli wyników</td></tr>\n" * 40_000).encode()
    assert page_cache.COMPRESS_MIN_BYTES < len(body) < page_cache.PAGE_CACHE_MAX_BYTES
    middleware = page_cache.PageCacheMiddleware(
        get_response=lambda req: HttpResponse(body, content_type="text/html; charset=utf-8")
    )

    def request():
        req = RequestFactory().get(f"/results/{competition.pk}/")
        req.competition, req.user, req.session = competition, AnonymousUser(), SessionStore()
        req.LANGUAGE_CODE, req.csp_nonce = "pl", "n"
        return req

    assert middleware(request())["X-Page-Cache"] == "MISS"
    stored = cache.get(page_cache.build_key(request()))
    hit = middleware(request())

    assert stored["zlib"] is True
    assert len(stored["body"]) < len(body) // 10
    assert hit["X-Page-Cache"] == "HIT"
    assert hit.content == body


# --- limit wysyłek ----------------------------------------------------------------------------------


def test_upload_limit_is_counted_per_account_not_per_address(participant):
    other = participant_of(participant.competition)
    first, second = RequestFactory().post("/x/"), RequestFactory().post("/x/")
    first.META["REMOTE_ADDR"] = second.META["REMOTE_ADDR"] = "10.0.0.1"
    first.user, second.user = participant.user, other.user

    assert "upload" in throttle.PER_USER_SCOPES
    assert throttle.user_throttle_keys("upload", first) != throttle.user_throttle_keys("upload", second)
    assert ":user:" in throttle.user_throttle_keys("upload", first)[0]

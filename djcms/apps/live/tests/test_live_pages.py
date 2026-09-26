"""Strony i wtyczki żywe (DJ-01f): API działa / kopia „stale” / API martwe; zadania przed otwarciem.

Każda strona renderuje się przez pełny stos (``cms.urls`` → szablon → wtyczki → klient API
z zamockowanym otwieraczem), a nie przez wywołanie wtyczki w izolacji: niezmienniki z § 7
(brak tytułu zadania przed ``opens_at``, 200 bez 500 przy martwym API, ``X-Djcms-Degraded``)
dotyczą tego, co widzi czytelnik, a nie tego, co zwraca jedna funkcja.
"""

from __future__ import annotations

import urllib.error
from datetime import timedelta

import pytest
from django.core.cache import cache

from apps.live import data
from apps.live.client import CACHE_PREFIX
from apps.live.middleware import HEADER as DEGRADED_HEADER

from .conftest import edition_dto, iso, problem_dto, stage_dto, stage_row

pytestmark = pytest.mark.django_db

#: Klucze bufora API konkursu z fixture'a (``kwantowa``) – ``djcms:api:v2:<slug>:<endpoint>``.
COMPETITION_PREFIX = CACHE_PREFIX + "kwantowa:"

UNAVAILABLE = "chwilowo niedostępne w tej wersji serwisu"
SECRET_TITLE = "Tajne zadanie o splątaniu"


def _html(response) -> str:
    assert response.status_code == 200
    return response.content.decode()


def _problems_payload(**overrides) -> dict:
    payload = {
        "edition": edition_dto(),
        "stage": stage_dto(),
        "stage_has_opened": True,
        "problems": [problem_dto(1, SECRET_TITLE), problem_dto(2, "Kubit na orbicie", statement_url=None)],
        "training_stage": None,
        "training_problems": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def problems_page(live_page):
    return live_page(
        "Zadania",
        "zadania",
        "dj/pages/problems.html",
        intro=[("TextPlugin", {"body": "<p>Wprowadzenie do zadań</p>"})],
        problems=[("ProblemsPlugin", {"closed_notice": ""})],
    )


# --- zadania --------------------------------------------------------------------------------------


def test_problems_after_opening_show_titles_and_pdf_links(client, api_up, problems_page):
    api_up.set("problems", _problems_payload())
    response = client.get("/zadania/")
    html = _html(response)
    assert DEGRADED_HEADER not in response
    assert SECRET_TITLE in html
    assert 'href="https://olimpiada.example/competitions/problems/51/statement/"' in html
    assert "brak pliku" in html  # zadanie 2 bez PDF-u
    assert "etap otwarty" in html
    assert "Etap I – I edycja 2026/2027" in html
    assert "pdf, jpg" in html
    assert '<div class="lead">' in html and "Wprowadzenie do zadań" in html


def test_problems_before_opening_show_only_the_notice(client, api_up, live_page):
    """Kryterium 4: przed ``opens_at`` w HTML nie ma tytułu zadania ani odnośnika do PDF-u."""
    live_page(
        "Zadania",
        "zadania",
        "dj/pages/problems.html",
        problems=[("ProblemsPlugin", {"closed_notice": "Zadania pojawią się 1 października."})],
    )
    api_up.set(
        "problems",
        _problems_payload(
            stage=stage_dto(opens_at=iso(timedelta(days=5))), stage_has_opened=False, problems=[]
        ),
    )
    html = _html(client.get("/zadania/"))
    assert "przed otwarciem" in html
    assert "Zadania pojawią się 1 października." in html
    assert SECRET_TITLE not in html
    assert "statement" not in html
    assert "<caption>Zadania etapu</caption>" not in html


def test_default_closed_notice(client, api_up, problems_page):
    api_up.set("problems", _problems_payload(stage_has_opened=False, problems=[]))
    assert data.DEFAULT_CLOSED_NOTICE in _html(client.get("/zadania/"))


@pytest.mark.parametrize(
    "overrides",
    [
        # API powiedziało „nieotwarty”, a mimo to przysłało zadania (błąd po stronie API).
        {"stage_has_opened": False},
        # API powiedziało „otwarty”, ale ``opens_at`` jest jeszcze przed nami według zegara dj.
        {"stage": stage_dto(opens_at=iso(timedelta(hours=2)))},
        # Brak albo zła data otwarcia – nie ma czym potwierdzić otwarcia, więc zamknięte.
        {"stage": stage_dto(opens_at=None)},
        {"stage": stage_dto(opens_at="jutro")},
        # ``stage_has_opened`` musi być prawdą logiczną, a nie „czymś niepustym”.
        {"stage_has_opened": "true"},
    ],
)
def test_problems_never_shown_unless_api_and_clock_agree(client, api_up, problems_page, overrides):
    api_up.set("problems", _problems_payload(**overrides))
    html = _html(client.get("/zadania/"))
    assert SECRET_TITLE not in html
    assert "statement" not in html
    assert "przed otwarciem" in html


def test_training_sheet(client, api_up, problems_page):
    training = stage_dto(id=20, display_name="Trening", is_training=True, kind="TRAINING")
    api_up.set(
        "problems",
        _problems_payload(
            training_stage=training,
            training_problems=[problem_dto(1, "Rozgrzewka", statement_url="javascript:alert(1)")],
        ),
    )
    html = _html(client.get("/zadania/"))
    assert 'id="zadania-treningowe"' in html
    assert "Arkusz treningowy: Trening" in html
    assert "Rozgrzewka" in html
    # Adres spoza http(s) nie trafia do ``href`` – druga warstwa po ``api_href`` aplikacji głównej.
    assert "javascript:" not in html


def test_training_sheet_follows_the_same_clock_rule(client, api_up, problems_page):
    training = stage_dto(id=20, display_name="Trening", is_training=True, opens_at=iso(timedelta(days=1)))
    api_up.set(
        "problems",
        _problems_payload(training_stage=training, training_problems=[problem_dto(1, "Przedwczesne")]),
    )
    html = _html(client.get("/zadania/"))
    assert "Przedwczesne" not in html
    assert "Arkusz treningowy jest jeszcze pusty." in html


def test_problems_without_stage(client, api_up, problems_page):
    api_up.set("problems", _problems_payload(edition=None, stage=None, stage_has_opened=False, problems=[]))
    assert "Nie ustawiono bieżącej edycji ani etapu." in _html(client.get("/zadania/"))


def test_problems_api_dead_degrades_to_200_with_message(client, main_api, problems_page):
    response = client.get("/zadania/")
    html = _html(response)
    assert response[DEGRADED_HEADER] == "1"
    assert UNAVAILABLE in html
    assert 'href="https://olimpiada.example/zadania/"' in html
    assert "Wprowadzenie do zadań" in html  # treść redakcyjna zostaje


def test_problems_no_competition_503_degrades(client, api_up, problems_page):
    api_up.set("problems", {"error": "no-competition"}, status=503)
    response = client.get("/zadania/")
    assert UNAVAILABLE in _html(response)
    assert response[DEGRADED_HEADER] == "1"


def test_problems_stale_copy_is_never_shown(client, api_up, problems_page):
    """Zadania z kopii „stale” – nie: otwarcie etapu da się wycofać, a kopia pokazywałaby je dalej."""
    api_up.set("problems", _problems_payload())
    assert SECRET_TITLE in _html(client.get("/zadania/"))
    # Świeży wpis wygasa, API pada: zamiast listy z kopii – komunikat „chwilowo niedostępne”.
    cache.delete(COMPETITION_PREFIX + "problems")
    api_up.fail("problems", urllib.error.URLError(TimeoutError()))
    response = client.get("/zadania/")
    html = _html(response)
    assert response[DEGRADED_HEADER] == "1"
    assert SECRET_TITLE not in html
    assert UNAVAILABLE in html


def test_stale_copy_from_before_opening_cannot_show_problems(client, api_up, problems_page):
    """Kopia zapisana przed otwarciem ma pustą listę – po wygaśnięciu bufora zadań nadal nie ma."""
    api_up.set("problems", _problems_payload(stage_has_opened=False, problems=[]))
    _html(client.get("/zadania/"))
    cache.delete(COMPETITION_PREFIX + "problems")
    api_up.fail("problems", urllib.error.URLError(TimeoutError()))
    html = _html(client.get("/zadania/"))
    assert SECRET_TITLE not in html
    assert UNAVAILABLE in html


def test_withdrawn_opening_disappears_after_short_fresh_ttl(client, api_up, problems_page, settings):
    """Wycofane otwarcie etapu: po krótkim buforze świeżym (``problems`` – 15 s, nie 60 s) strona
    pokazuje stan z API; kopia sprzed wycofania nie wraca ani ze świeżego bufora, ani ze „stale”."""
    from apps.live import client as api_client

    settings.DJCMS_API_CACHE_SECONDS = 60
    assert api_client.fresh_seconds("problems") == api_client.SHORT_CACHE_SECONDS == 15
    assert api_client.fresh_seconds("results") == 15
    assert api_client.fresh_seconds("editions/3/results") == 15
    assert api_client.fresh_seconds("chrome") == 60
    api_up.set("problems", _problems_payload())
    assert SECRET_TITLE in _html(client.get("/zadania/"))
    # Otwarcie wycofane w aplikacji głównej; bufor świeży wygasł (tu: skasowany).
    api_up.set("problems", _problems_payload(stage_has_opened=False, problems=[]))
    cache.delete(COMPETITION_PREFIX + "problems")
    assert SECRET_TITLE not in _html(client.get("/zadania/"))
    # API pada – kopia „stale” (już po wycofaniu) i tak nie jest pokazywana jako lista.
    cache.delete(COMPETITION_PREFIX + "problems")
    api_up.fail("problems", urllib.error.URLError(TimeoutError()))
    html = _html(client.get("/zadania/"))
    assert SECRET_TITLE not in html and UNAVAILABLE in html


def test_problems_fresh_cache_uses_the_short_ttl(api_up, rf):
    from apps.live import client as api_client

    api_up.set("problems", _problems_payload())
    calls = []
    real_set = cache.set

    def spy(key, value, timeout=None, *args, **kwargs):
        calls.append((key, timeout))
        return real_set(key, value, timeout, *args, **kwargs)

    cache.set = spy
    try:
        api_client.MainApi().get("problems", competition="kwantowa")
    finally:
        cache.set = real_set
    assert (COMPETITION_PREFIX + "problems", 15) in calls


# --- wyniki ---------------------------------------------------------------------------------------


def _results_payload(**overrides) -> dict:
    published = stage_dto(results_url="https://olimpiada.example/results/12/")
    payload = {
        "edition": edition_dto(),
        "tables": [
            {
                "stage": published,
                "publication": {
                    "id": 9,
                    "published_at": iso(timedelta(days=-1)),
                    "published_at_local_time": "25 września 2026 12:00 (czas polski)",
                    "published_at_local_datetime": "25 września 2026 12:00",
                    "anonymization": "CODE",
                    "anonymization_display": "kody uczestników",
                },
                "problem_numbers": ["1", "2", "3"],
                "problem_maxima": {"1": "6", "3": "12,5"},
                "rows": [
                    {
                        "rank": 1,
                        "display": "OLM-7Q2K",
                        "district": "mazowieckie",
                        "points": {"1": 6, "2": 4.25},
                        "points_display": {"1": "6", "2": "4,25"},
                        "total": 10.25,
                        "total_display": "10,25",
                        "qualified": True,
                        "manual": False,
                    },
                    {
                        "rank": 2,
                        "display": "OLM-9ZZZ",
                        "points": {"1": 1},
                        "points_display": {"1": "1"},
                        "total": 1,
                        "total_display": "1",
                        "qualified": False,
                        "manual": True,
                    },
                ],
            }
        ],
        "archive": [
            {
                "stage": stage_dto(
                    id=4,
                    display_name="Finał",
                    edition_year_label="0 edycja 2025/2026",
                    results_url="https://olimpiada.example/results/4/",
                ),
                "publication": {
                    "id": 2,
                    "published_at_local_time": "1 czerwca 2026 10:00 (czas polski)",
                    "anonymization_display": "pseudonimy",
                },
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def results_page(live_page):
    return live_page("Wyniki", "wyniki", "dj/pages/results.html", results=[("ResultsPlugin", {})])


def test_results_tables_from_snapshot(client, api_up, results_page):
    api_up.set("results", _results_payload())
    response = client.get("/wyniki/")
    html = _html(response)
    assert DEGRADED_HEADER not in response
    assert 'class="table table--rank table--sticky-rank"' in html
    assert "OLM-7Q2K" in html and "mazowieckie" in html
    assert 'Zad. 1 <span class="hint">(max 6)</span>' in html
    assert "<th>Zad. 2</th>" in html  # maksimum nieznane – bez dopisku
    assert 'Zad. 3 <span class="hint">(max 12,5)</span>' in html
    assert '<td class="num">4,25</td>' in html
    assert '<td class="num total">10,25</td>' in html
    assert 'class="is-qualified"' in html
    assert "kwalifikacja decyzją komitetu" in html
    assert "Ogłoszone 25 września 2026 12:00 (czas polski), tryb prezentacji:" in html
    assert 'href="https://olimpiada.example/results/12/">wersja pełnoekranowa' in html
    assert "Wcześniejsze edycje" in html
    assert 'href="https://olimpiada.example/results/4/"' in html
    assert "Wyników jeszcze nie ogłoszono" not in html


def test_results_row_without_points_for_a_problem_has_empty_cell(client, api_up, results_page):
    api_up.set("results", _results_payload(archive=[]))
    html = _html(client.get("/wyniki/"))
    # Drugi wiersz: zadanie 1 = „1”, zadania 2 i 3 bez punktów – puste komórki, jak ``dict_get|points``.
    row = html.split("OLM-9ZZZ", 1)[1].split("</tr>", 1)[0]
    assert row.count('<td class="num"></td>') == 2


def test_results_empty_state(client, api_up, results_page):
    api_up.set("results", {"edition": None, "tables": [], "archive": []})
    assert "Wyników jeszcze nie ogłoszono" in _html(client.get("/wyniki/"))


def test_results_unsafe_url_is_dropped(client, api_up, results_page):
    payload = _results_payload()
    payload["tables"][0]["stage"]["results_url"] = "javascript:alert(1)"
    api_up.set("results", payload)
    html = _html(client.get("/wyniki/"))
    assert "javascript:" not in html
    assert "wersja pełnoekranowa" not in html


def test_results_api_dead(client, main_api, results_page):
    response = client.get("/wyniki/")
    assert UNAVAILABLE in _html(response)
    assert response[DEGRADED_HEADER] == "1"


def test_withdrawn_results_publication_is_not_shown_from_stale_copy(client, api_up, results_page):
    """Publikacja wyników wycofana: świeży stan z API bez tabel; kopia „stale” sprzed wycofania
    nigdy nie trafia na stronę jako tabela – przy martwym API komunikat, a nie wyniki."""
    api_up.set("results", _results_payload())
    assert "OLM-7Q2K" in _html(client.get("/wyniki/"))
    api_up.set("results", {"edition": edition_dto(), "tables": [], "archive": []})
    cache.delete(COMPETITION_PREFIX + "results")
    html = _html(client.get("/wyniki/"))
    assert "OLM-7Q2K" not in html and "Wyników jeszcze nie ogłoszono" in html
    # Kopia „stale” z tabelą (sprzed wycofania) + martwe API → komunikat, bez tabeli.
    api_up.set("results", _results_payload())
    cache.delete(COMPETITION_PREFIX + "results")
    _html(client.get("/wyniki/"))
    cache.delete(COMPETITION_PREFIX + "results")
    api_up.fail("results", urllib.error.URLError(TimeoutError()))
    html = _html(client.get("/wyniki/"))
    assert "OLM-7Q2K" not in html and UNAVAILABLE in html


# --- archiwum edycji ------------------------------------------------------------------------------


@pytest.fixture
def archive_page(live_page):
    return live_page(
        "Edycja 2025/2026",
        "edycja-2025",
        "dj/pages/archive_edition.html",
        results=[("ArchiveResultsPlugin", {})],
    )


def test_archive_links(client, api_up, archive_page, monkeypatch):
    monkeypatch.setattr(data, "archive_edition_id", lambda content: 2)
    api_up.set(
        "editions/2/results",
        {
            "edition": edition_dto(id=2, year_label="0 2025/2026"),
            "links": [
                {
                    "stage": stage_dto(id=4, display_name="Finał"),
                    "results_url": "https://olimpiada.example/results/4/",
                },
                {"stage": stage_dto(id=5, display_name="Zły"), "results_url": "javascript:alert(1)"},
            ],
        },
    )
    response = client.get("/edycja-2025/")
    html = _html(response)
    assert DEGRADED_HEADER not in response
    assert "Edycja: 0 2025/2026" in html
    assert '<a href="https://olimpiada.example/results/4/">Finał – tabela wyników</a>' in html
    assert "Zły" not in html and "javascript:" not in html
    assert "Do tej edycji nie dołączono jeszcze dokumentów." in html
    assert api_up.calls("editions/2/results") == 1  # nagłówek strony i wtyczka – jedno pobranie


def test_archive_without_linked_edition(client, api_up, archive_page):
    html = _html(client.get("/edycja-2025/"))
    assert "Dla tej edycji nie ogłoszono tabel wyników." in html
    assert "Edycja:" not in html


def test_archive_api_dead(client, main_api, archive_page, monkeypatch):
    monkeypatch.setattr(data, "archive_edition_id", lambda content: 2)
    response = client.get("/edycja-2025/")
    assert UNAVAILABLE in _html(response)
    assert response[DEGRADED_HEADER] == "1"


def test_archive_results_fallback_when_plugin_removed(client, api_up, live_page, monkeypatch):
    live_page("Edycja", "edycja", "dj/pages/archive_edition.html")
    monkeypatch.setattr(data, "archive_edition_id", lambda content: 2)
    api_up.set(
        "editions/2/results",
        {
            "edition": None,
            "links": [
                {
                    "stage": stage_dto(display_name="Finał"),
                    "results_url": "https://olimpiada.example/results/4/",
                }
            ],
        },
    )
    assert "Finał – tabela wyników" in _html(client.get("/edycja/"))


def test_archive_edition_id_reads_the_page_extension():
    class Meta:
        edition_id = 7

    class Content:
        archivemeta = Meta()

    class Missing:
        @property
        def archivemeta(self):
            from django.core.exceptions import ObjectDoesNotExist

            raise ObjectDoesNotExist

    assert data.archive_edition_id(Content()) == 7
    assert data.archive_edition_id(Missing()) is None
    assert data.archive_edition_id(object()) is None
    assert data.archive_edition_id(None) is None
    Meta.edition_id = None
    assert data.archive_edition_id(Content()) is None


# --- strona główna --------------------------------------------------------------------------------


def _stages_payload(**overrides) -> dict:
    current = stage_dto()
    done = stage_dto(id=11, display_name="Etap 0", results_url="https://olimpiada.example/results/11/")
    final = stage_dto(id=13, display_name="Finał", location="Kraków")
    payload = {
        "edition": edition_dto(),
        "current_stage": current,
        "rows": [
            stage_row(
                done, is_open=False, has_results=True, status="results", status_label="wyniki ogłoszone"
            ),
            stage_row(current),
            stage_row(
                final,
                is_open=False,
                has_opened=False,
                status="upcoming",
                status_label="nadchodzący",
                is_onsite_event=True,
                date_range="4–7 czerwca 2027",
            ),
        ],
    }
    payload.update(overrides)
    return payload


def _workshops_payload() -> dict:
    return {
        "page_path": "/warsztaty/",
        "upcoming": [
            {
                "topic": "Kubity",
                "date": "9 października 2026",
                "date_value": "2026-10-09",
                "time": "17:00–18:30",
                "lecturer": "",
            },
            {
                "topic": "Splątanie",
                "date": "16 października 2026",
                "date_value": "2026-10-16",
                "time": "",
                "lecturer": "",
            },
        ],
        "rows": [],
        "materials": {
            "show": True,
            "count": 4,
            "login_url": "https://olimpiada.example/login/?next=/warsztaty/materialy/",
            "materials_url": "https://olimpiada.example/warsztaty/materialy/",
        },
    }


@pytest.fixture
def home_page(live_page):
    return live_page(
        "Strona główna",
        "start",
        "dj/pages/home.html",
        home=True,
        timeline=[("StageTimelinePlugin", {"variant": "home"})],
    )


def test_home_with_live_data(client, api_up, home_page):
    api_up.set("stages", _stages_payload())
    api_up.set("workshops", _workshops_payload())
    response = client.get("/")
    html = _html(response)
    assert DEGRADED_HEADER not in response
    # hero: nadtytuł z edycji, tytuł strony (pusty slot „hero”), przyciski, etap bieżący
    assert '<span class="eyebrow">I Edycja 2026/2027</span>' in html
    assert "<h1>Strona główna</h1>" in html
    assert '<a class="btn btn--accent" href="https://olimpiada.example/register/">Zarejestruj się</a>' in html
    assert '<a class="btn btn--secondary" href="https://olimpiada.example/login/">Zaloguj się</a>' in html
    assert "Etap bieżący: <strong>Etap I</strong>" in html
    assert "termin oddania rozwiązań 10 października 2026 23:59 (czas polski)" in html
    # oś czasu (wariant „strona główna”)
    assert "Przebieg zawodów: I edycja 2026/2027" in html
    assert 'href="https://olimpiada.example/results/11/">Tabela wyników</a>' in html
    assert "<dt>Termin</dt><dd>4–7 czerwca 2027</dd>" in html
    assert "<dt>Miejsce</dt><dd>Kraków</dd>" in html
    assert "<dt>Deadline</dt><dd>10 października 2026 23:59</dd>" in html
    assert "etap otwarty" in html and "zakończony" in html and "nadchodzący" in html
    # warsztaty
    assert 'id="warsztaty"' in html
    assert "9 października 2026 · 17:00–18:30" in html
    assert '<p class="card__meta">16 października 2026</p>' in html
    assert 'href="/warsztaty/">Zobacz wszystkie warsztaty' in html
    assert "<title>Olimpiada Testowa</title>" in html


def test_home_registration_not_yet(client, main_api, chrome_payload, home_page):
    main_api.set(
        "chrome",
        chrome_payload(
            registration={
                "is_open": False,
                "reason": "not_yet",
                "opens_at": "2026-10-08T00:00:00+02:00",
                "opens_at_display": "8 października 2026",
                "closes_at": None,
                "message": "",
            },
            site={**chrome_payload()["site"], "registration_note": "Oficjalny start 8 października."},
        ),
    )
    main_api.set("stages", _stages_payload(current_stage=None))
    main_api.set("workshops", {**_workshops_payload(), "upcoming": []})
    html = _html(client.get("/"))
    assert "Rejestracja rusza 8 października 2026" in html
    assert '<p class="hero__note">Oficjalny start 8 października.</p>' in html
    assert "hero__status" not in html
    assert 'id="warsztaty"' not in html  # brak nadchodzących warsztatów = brak sekcji


def test_home_registration_closed_has_only_login(client, main_api, chrome_payload, home_page):
    main_api.set(
        "chrome", chrome_payload(registration={"is_open": False, "reason": "closed", "opens_at_display": ""})
    )
    main_api.set("stages", _stages_payload())
    html = _html(client.get("/"))
    assert "Zarejestruj się" not in html and "Rejestracja rusza" not in html
    assert "Zaloguj się</a>" in html


def test_home_without_edition(client, api_up, home_page):
    api_up.set("stages", {"edition": None, "current_stage": None, "rows": []})
    assert "Nie ustawiono bieżącej edycji olimpiady." in _html(client.get("/"))


def test_home_api_dead(client, main_api, home_page):
    """Kryterium 7: martwe API = 200, komunikat w sekcji terminów, rama z logowaniem, bez 500."""
    response = client.get("/")
    html = _html(response)
    assert response[DEGRADED_HEADER] == "1"
    assert UNAVAILABLE in html
    assert "<h1>Strona główna</h1>" in html
    assert "Zaloguj się</a>" in html
    assert "Zarejestruj się" not in html
    assert 'class="eyebrow"' not in html
    assert 'id="warsztaty"' not in html


def test_home_stale(client, api_up, home_page):
    api_up.set("stages", _stages_payload())
    _html(client.get("/"))
    cache.delete(COMPETITION_PREFIX + "stages")
    api_up.fail("stages", urllib.error.URLError(TimeoutError()))
    response = client.get("/")
    html = _html(response)
    assert response[DEGRADED_HEADER] == "1"
    assert "stan na " in html
    assert "Przebieg zawodów" in html


def test_home_hero_slot_with_text_plugin_replaces_title_fallback(client, api_up, live_page):
    live_page(
        "Start", "start", "dj/pages/home.html", home=True, hero=[("TextPlugin", {"body": "<h1>Hasło</h1>"})]
    )
    html = _html(client.get("/"))
    assert "<h1>Hasło</h1>" in html
    assert "<h1>Start</h1>" not in html


# --- terminy etapów w treści (wariant „block”) ----------------------------------------------------


def test_stage_timeline_block_variant(client, api_up, live_page):
    live_page(
        "Harmonogram",
        "harmonogram",
        "dj/pages/problems.html",
        body=[("StageTimelinePlugin", {"variant": "block", "heading": "Terminy etapów"})],
    )
    api_up.set("problems", _problems_payload())
    grace = stage_dto(id=14, display_name="Etap II", grace_seconds=900)
    interview = stage_dto(id=15, display_name="Rozmowa", is_interview=True)
    api_up.set(
        "stages",
        _stages_payload(
            rows=[
                stage_row(grace, badge_class="badge badge--neutral", status_label="nadchodzący"),
                stage_row(interview),
            ]
        ),
    )
    html = _html(client.get("/harmonogram/"))
    assert '<h2 class="stage-timeline__heading">Terminy etapów</h2>' in html
    assert '<span class="badge badge--neutral">nadchodzący</span>' in html
    assert "<dt>Oddanie rozwiązań</dt>" in html
    assert "<dt>Upload zamyka się</dt>" in html
    assert "<dt>Forma</dt><dd>rozmowa kwalifikacyjna online</dd>" in html
    assert "<dt>Rozmowy do</dt>" in html
    assert "<dt>Wyniki do</dt><dd>24 października 2026 23:59</dd>" in html


def test_stage_timeline_block_without_rows(client, api_up, live_page):
    live_page("Harm", "harm", "dj/pages/problems.html", body=[("StageTimelinePlugin", {"variant": "block"})])
    api_up.set("problems", _problems_payload())
    api_up.set("stages", {"edition": None, "current_stage": None, "rows": []})
    assert "Terminy zostaną ogłoszone" in _html(client.get("/harm/"))


# --- zapowiedź materiałów z warsztatów ------------------------------------------------------------


def _render_teaser(rf):
    from django.template import engines

    from apps.sites.models import CompetitionSite

    request = rf.get("/warsztaty/")
    # To, co ustawia ``CompetitionSiteMiddleware`` – klient API bierze konkurs z żądania.
    request.competition_site = CompetitionSite.objects.get(slug="kwantowa")
    template = engines["django"].from_string("{% load dj_live %}{% dj_workshop_materials_teaser %}")
    return template.render({}, request)


def test_workshop_materials_teaser(rf, api_up):
    api_up.set("workshops", _workshops_payload())
    html = _render_teaser(rf)
    assert "Materiały z warsztatów" in html
    assert "materiały do pobrania (4)" in html
    assert 'href="https://olimpiada.example/login/?next=/warsztaty/materialy/"' in html


def test_workshop_materials_teaser_hidden_when_off_or_dead(rf, main_api):
    assert "Materiały" not in _render_teaser(rf)
    main_api.set("workshops", {**_workshops_payload(), "materials": {"show": False, "count": 0}})
    cache.clear()
    assert "Materiały" not in _render_teaser(rf)

"""``apps.cms.live_data`` – dane zawodów wspólne dla stron Wagtaila i API wersji ``dj.`` (DJ-01 § 4).

Trzy rzeczy, które te testy pilnują:

1. **te same reguły, co dotąd** – terminy wyłącznie z bazy, zadania dopiero po ``opens_at`` (także
   treningowe), wyniki wyłącznie z ogłoszonych snapshotów tego konkursu. Każda z funkcji dostaje
   świat, w którym dana reguła ma coś do odrzucenia,
2. **jedno źródło dla dwóch wersji serwisu** – kontekst wyrenderowanej strony Wagtaila jest równy
   temu, co oddaje funkcja wspólna dla tego samego świata. Gdyby strona zaczęła liczyć po swojemu,
   ``dj.`` pokazywałoby co innego niż strona główna,
3. **koszt nie rośnie z danymi** – liczba zapytań nie zależy od liczby etapów ani ogłoszonych tabel
   (ta sama reguła, co w budżetach zapytań stron).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.cms.live_data import (
    CompetitionState,
    archive_result_links,
    competition_state,
    problems_state,
    results_state,
)
from apps.cms.models import ArchiveEditionPage
from apps.competitions.models import TRAINING_DEADLINE, StageKind
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    EditionFactory,
    ProblemFactory,
    StageFactory,
)
from apps.results.models import Anonymization, ResultsPublication

pytestmark = pytest.mark.django_db

NOW = timezone.now()

SNAPSHOT = [
    {"rank": 1, "display": "OLM-AAAAAA", "district": "mazowieckie", "points": {"1": 6, "2": 5}, "total": 11},
]


def publish(stage, *, withdrawn: bool = False) -> ResultsPublication:
    """Ogłoszenie tabeli; ``withdrawn`` = koordynator zdjął znacznik publikacji z etapu."""
    stage.results_published_at = None if withdrawn else timezone.now()
    stage.save(update_fields=["results_published_at"])
    return ResultsPublication.objects.create(stage=stage, anonymization=Anonymization.CODE, snapshot=SNAPSHOT)


def training_stage_for(edition, *, opens_at=None):
    return StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        opens_at=opens_at or datetime(2020, 1, 1, tzinfo=timezone.get_current_timezone()),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE + timedelta(days=14),
        appeal_window_opens_at=TRAINING_DEADLINE + timedelta(days=16),
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=23),
    )


# --- świat bez edycji / edycja bez etapów ---------------------------------------------------------


def test_without_an_edition_every_function_answers_empty(competition):
    assert competition_state(competition, NOW) == CompetitionState(None, None, [])
    problems = problems_state(competition, NOW)
    assert (problems.edition, problems.stage, problems.stage_has_opened) == (None, None, False)
    assert problems.problems == [] and problems.training_stage is None and problems.training_problems == []
    results = results_state(competition)
    assert (results.edition, results.tables, results.archive) == (None, [], [])


def test_an_edition_without_stages_has_no_current_stage_and_no_rows(competition):
    edition = CurrentEditionFactory(competition=competition)

    state = competition_state(competition, NOW)

    assert state.edition == edition
    assert state.current_stage is None
    assert state.stage_rows == []
    assert problems_state(competition, NOW).stage is None


# --- zadania: jawne dopiero po opens_at -----------------------------------------------------------


def test_problems_before_opening_are_withheld(competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition, opens_at=NOW + timedelta(days=2))
    ProblemFactory(stage=stage, number=1, title="Tajne zadanie przed otwarciem")

    state = problems_state(competition, NOW)

    assert state.stage == stage
    assert state.stage_has_opened is False
    assert state.problems == []


def test_problems_after_opening_are_listed_in_order(competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition, opens_at=NOW - timedelta(hours=1))
    second = ProblemFactory(stage=stage, number=2, title="Drugie")
    first = ProblemFactory(stage=stage, number=1, title="Pierwsze")

    state = problems_state(competition, NOW)

    assert state.stage_has_opened is True
    assert state.problems == [first, second]


def test_training_problems_follow_the_same_opening_rule(competition):
    edition = CurrentEditionFactory(competition=competition)
    training = training_stage_for(edition, opens_at=NOW + timedelta(days=1))
    ProblemFactory(stage=training, number=1, title="Trening jeszcze zamknięty")

    closed = problems_state(competition, NOW)
    opened = problems_state(competition, NOW + timedelta(days=2))

    assert closed.training_stage == training
    assert closed.training_problems == []
    assert [problem.title for problem in opened.training_problems] == ["Trening jeszcze zamknięty"]


def test_training_is_never_the_current_stage_nor_a_timeline_row(competition):
    edition = CurrentEditionFactory(competition=competition)
    training_stage_for(edition)

    state = competition_state(competition, NOW)

    assert state.current_stage is None
    assert state.stage_rows == []


# --- wyniki: wyłącznie ogłoszone snapshoty tego konkursu -------------------------------------------


def test_results_split_current_edition_tables_from_archive(competition):
    old_edition = EditionFactory(competition=competition, year_label="Stara edycja")
    old_stage = StageFactory(edition=old_edition, opens_at=NOW - timedelta(days=400))
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition)
    old_publication = publish(old_stage)
    publication = publish(stage)

    state = results_state(competition)

    assert state.edition == edition
    assert [table["publication"] for table in state.tables] == [publication]
    assert state.tables[0]["rows"] == SNAPSHOT
    assert state.tables[0]["problem_numbers"] == ["1", "2"]
    assert state.archive == [{"stage": old_stage, "publication": old_publication}]


def test_a_withdrawn_publication_is_neither_a_table_nor_an_archive_link(competition):
    edition = CurrentEditionFactory(competition=competition)
    publish(StageFactory(edition=edition), withdrawn=True)

    state = results_state(competition)

    assert state.tables == []
    assert state.archive == []


def test_publications_of_another_competition_never_leak(competition, other_competition):
    CurrentEditionFactory(competition=competition)
    foreign_edition = CurrentEditionFactory(competition=other_competition)
    foreign = publish(StageFactory(competition=other_competition, edition=foreign_edition))

    state = results_state(competition)
    foreign_state = results_state(other_competition)

    assert state.tables == [] and state.archive == []
    assert [table["publication"] for table in foreign_state.tables] == [foreign]


def test_results_cost_does_not_grow_with_published_stages(competition):
    edition = CurrentEditionFactory(competition=competition)
    publish(StageFactory(edition=edition))
    with CaptureQueriesContext(connection) as one:
        results_state(competition)
    for _ in range(3):
        publish(StageFactory(edition=EditionFactory(competition=competition)))
    publish(StageFactory(edition=edition, kind=StageKind.DISTRICT))
    with CaptureQueriesContext(connection) as many:
        state = results_state(competition)

    assert len(state.tables) == 2 and len(state.archive) == 3
    # Maksima zadań czytają skalę etapu – jedno zapytanie na tabelę bieżącej edycji, nie na wiersz.
    assert len(many) <= len(one) + len(state.tables)


def test_timeline_cost_does_not_grow_with_stages(competition):
    edition = CurrentEditionFactory(competition=competition)
    StageFactory(edition=edition)
    with CaptureQueriesContext(connection) as one:
        competition_state(competition, NOW)
    for kind in (StageKind.DISTRICT, StageKind.FINAL):
        StageFactory(edition=edition, kind=kind, opens_at=NOW + timedelta(days=30))
    with CaptureQueriesContext(connection) as three:
        state = competition_state(competition, NOW)

    assert len(state.stage_rows) == 3
    assert len(three) == len(one)


# --- archiwum -------------------------------------------------------------------------------------


def test_archive_links_only_for_published_stages(competition):
    edition = EditionFactory(competition=competition)
    published = StageFactory(edition=edition, opens_at=NOW - timedelta(days=40))
    StageFactory(edition=edition, kind=StageKind.DISTRICT, opens_at=NOW - timedelta(days=20))
    publish(published)

    assert archive_result_links(edition.pk) == [{"stage": published}]
    assert archive_result_links(edition.pk, competition) == [{"stage": published}]
    assert archive_result_links(None) == []


def test_archive_links_of_a_foreign_edition_are_empty_when_the_competition_is_given(
    competition, other_competition
):
    foreign_edition = EditionFactory(competition=other_competition)
    publish(StageFactory(competition=other_competition, edition=foreign_edition))

    assert archive_result_links(foreign_edition.pk, competition) == []
    assert len(archive_result_links(foreign_edition.pk, other_competition)) == 1


# --- strona Wagtaila i funkcja wspólna dają to samo -----------------------------------------------


def test_home_page_context_is_the_competition_state(web_client, competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition)
    StageFactory(edition=edition, kind=StageKind.FINAL, opens_at=NOW + timedelta(days=60))
    publish(stage)

    response = web_client.get("/")
    state = competition_state(competition)

    assert response.status_code == 200
    assert response.context["edition"] == state.edition
    assert response.context["current_stage"] == state.current_stage
    assert response.context["stage_rows"] == state.stage_rows


def test_problems_page_context_is_the_problems_state(web_client, competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition)
    ProblemFactory(stage=stage, number=1)
    training_stage_for(edition)

    response = web_client.get("/zadania/")
    state = problems_state(competition)

    assert response.status_code == 200
    for key in ("edition", "stage", "stage_has_opened", "problems", "training_stage", "training_problems"):
        assert response.context[key] == getattr(state, key), key


def test_problems_page_before_opening_has_no_problem_in_context(web_client, competition):
    edition = CurrentEditionFactory(competition=competition)
    stage = StageFactory(edition=edition, opens_at=NOW + timedelta(days=3))
    ProblemFactory(stage=stage, number=1, title="Zadanie z embargiem")

    response = web_client.get("/zadania/")

    assert response.context["problems"] == []
    assert "Zadanie z embargiem" not in response.content.decode()


def test_results_page_context_is_the_results_state(web_client, competition):
    edition = CurrentEditionFactory(competition=competition)
    publish(StageFactory(edition=edition))
    publish(StageFactory(edition=EditionFactory(competition=competition)))

    response = web_client.get("/wyniki/")
    state = results_state(competition)

    assert response.context["edition"] == state.edition
    assert response.context["tables"] == state.tables
    assert response.context["archive"] == state.archive


def test_archive_edition_page_links_are_the_shared_function(archive_index, competition):
    edition = EditionFactory(competition=competition)
    stage = StageFactory(edition=edition)
    publish(stage)
    page = archive_index.add_child(
        instance=ArchiveEditionPage(title="Edycja archiwalna", slug="edycja-archiwalna", edition=edition)
    )

    assert page.result_links() == archive_result_links(edition.pk) == [{"stage": stage}]

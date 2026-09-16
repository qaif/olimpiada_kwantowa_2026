"""Materiały i zadania archiwalne: ``GET /me/archive/``.

Czego pilnują te testy:

- **archiwum nie wyprzedza zawodów.** Zadania etapu, który się jeszcze nie otworzył, nie mogą
  się w nim pojawić – nawet z minionej edycji. Tę regułę egzekwuje też sam widok pliku
  (``competitions.ProblemStatementView``), ale odnośnik do 404 też jest usterką,
- **bieżąca edycja nie jest archiwum.** Zadania trwających zawodów mieszkają w panelu, razem
  z uploadem; skopiowane tutaj byłyby drugim miejscem, w którym trzeba pilnować terminu,
- **trening zostaje do rozwiązania „na sucho”** i prowadzi na kartę treningową pulpitu,
  zamiast powielać formularz wysyłki.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.competitions.models import TRAINING_DEADLINE, StageKind
from apps.competitions.tests.factories import (
    EditionFactory,
    ProblemFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.web.views.participant_tools import archived_editions

pytestmark = pytest.mark.django_db

ARCHIVE_URL = "/me/archive/"


@pytest.fixture
def past_edition():
    return EditionFactory(year_label="XIV (2025/2026)")


@pytest.fixture
def archived_problem(past_edition):
    """Zadanie zakończonej edycji – etap otwarty dawno temu, więc jego treść jest jawna."""
    stage = StageFactory(edition=past_edition, kind=StageKind.ELIM, name="Eliminacje 2025")
    return ProblemFactory(stage=stage, number=1, title="Interferometr Macha-Zehndera")


@pytest.fixture
def training(edition):
    """Etap treningowy bieżącej edycji – bez terminu, z jednym zadaniem."""
    stage = StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        name="Zadania treningowe",
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE,
        appeal_window_opens_at=TRAINING_DEADLINE,
    )
    ProblemFactory(stage=stage, number=1, title="P1. Cząstka w studni potencjału")
    return stage


# --- dobór zawartości ------------------------------------------------------------------------------


def test_past_edition_problems_are_listed(archived_problem):
    rows = archived_editions()

    titles = [row.problem.title for edition in rows for stage in edition.stages for row in stage.problems]
    assert archived_problem.title in titles


def test_problems_of_an_unopened_stage_are_not_listed(past_edition):
    """Treść zadania jest jawna dopiero po ``opens_at`` – odnośnik wcześniej prowadziłby do 404."""
    stage = StageFactory(
        edition=past_edition, kind=StageKind.FINAL, opens_at=timezone.now() + timedelta(days=30)
    )
    ProblemFactory(stage=stage, number=1, title="Zadanie z przyszłości")

    assert archived_editions() == []


def test_current_edition_is_not_archive(edition, problems):
    """Zadania trwających zawodów mieszkają w panelu, razem z uploadem."""
    assert archived_editions() == []


def test_training_stage_is_not_in_the_archived_editions(training):
    """Trening ma własną sekcję – wolno w nim jeszcze oddać rozwiązanie."""
    assert archived_editions() == []


# --- widok ----------------------------------------------------------------------------------------


def test_archive_page_shows_editions_training_and_workshops(
    web_client, participant, archived_problem, training
):
    web_client.force_login(participant.user)

    content = web_client.get(ARCHIVE_URL).content.decode()

    assert archived_problem.title in content
    assert "XIV (2025/2026)" in content
    assert "Zadania treningowe" in content
    assert "P1. Cząstka w studni potencjału" in content
    assert "Warsztaty" in content


def test_archive_links_the_training_upload_flow(web_client, participant, training):
    web_client.force_login(participant.user)

    content = web_client.get(ARCHIVE_URL).content.decode()

    assert "/me/#trening" in content
    assert "Zgłoś się do treningu" in content


def test_registered_participant_is_invited_to_solve_the_training_problems(web_client, participant, training):
    StageEntryFactory(participant=participant, stage=training)
    web_client.force_login(participant.user)

    content = web_client.get(ARCHIVE_URL).content.decode()

    assert "na sucho" in content


def test_archive_has_no_upload_form(web_client, participant, archived_problem, training):
    """Ekran jest wyłącznie do czytania – formularz wysyłki jest w panelu i tylko tam."""
    web_client.force_login(participant.user)

    content = web_client.get(ARCHIVE_URL).content.decode()

    assert 'name="file"' not in content


def test_archive_is_participants_only(web_client, reviewer):
    web_client.force_login(reviewer.user)

    assert web_client.get(ARCHIVE_URL).status_code == 403


def test_archive_requires_login(web_client):
    assert web_client.get(ARCHIVE_URL).status_code == 302

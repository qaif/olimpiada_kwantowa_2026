"""Etap treningowy na stronach publicznych: widoczny w „Zadaniach”, nieobecny w harmonogramie.

Dwie reguły, które łatwo pomylić i które łamią się osobno:

- ``/zadania/`` **pokazuje** trening w drugiej sekcji – to jedyne publiczne miejsce, z którego
  uczestnik dowiaduje się, że piaskownica istnieje,
- oś czasu (``stage_rows``) i strona główna **go nie mają** – bo ogłaszają harmonogram zawodów,
  a trening nie jest zawodami i nie ma terminu.

Gdyby druga reguła przestała obowiązywać, trening stanąłby na końcu tabeli jako etap „otwarty”
z datą 2099, czyli jako ten, na który wszyscy czekają najdłużej.

Trzecia reguła dotyczy ``/wyniki/``: tabela treningu **jest** ogłaszana (piaskownica ma przejść całą
ścieżkę), ale nosi odznakę „trening” – bez niej wyglądałaby dokładnie jak wynik zawodów.
"""

from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.cms.timeline import stage_rows
from apps.competitions.models import TRAINING_DEADLINE, Problem, StageKind
from apps.competitions.tests.factories import (
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageFactory,
)
from apps.results.models import Anonymization, ResultsPublication

pytestmark = pytest.mark.django_db


@pytest.fixture
def training(edition):
    """Etap treningowy bieżącej edycji – otwarty, z datą-wartownikiem zamiast terminu."""
    stage = StageFactory(
        edition=edition,
        kind=StageKind.TRAINING,
        name="Zadania treningowe",
        opens_at=timezone.now() - timedelta(days=1),
        deadline_at=TRAINING_DEADLINE,
        review_deadline_at=TRAINING_DEADLINE,
        appeal_window_opens_at=TRAINING_DEADLINE,
        appeal_window_closes_at=TRAINING_DEADLINE + timedelta(days=1),
    )
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, min_points=0)
    return stage


def test_zadania_pokazuja_sekcje_treningowa(web_client, training):
    for number in range(1, 5):
        Problem.objects.create(stage=training, number=number, title=f"Zadanie treningowe {number}")

    content = web_client.get("/zadania/").content.decode()

    assert "Zadania treningowe" in content
    for number in range(1, 5):
        assert f"Zadanie treningowe {number}" in content
    assert "Bez terminu" in content
    # Rok z daty-wartownika nie może wyciec na stronę pod żadnym podpisem.
    assert "2099" not in content


def test_sekcja_treningowa_dziala_bez_etapu_zawodow(web_client, training):
    """Edycja bez żadnego etapu zawodów: karta wyżej pokazuje komunikat, trening zostaje.

    To jest cały powód, dla którego sekcja stoi poza blokiem `{% if stage %}` – między etapami
    (albo przed pierwszym) trening bywa jedynym otwartym miejscem w portalu.
    """
    Problem.objects.create(stage=training, number=1, title="Zadanie treningowe 1")

    content = web_client.get("/zadania/").content.decode()

    assert "Zadania treningowe" in content
    assert "Zadanie treningowe 1" in content


def test_zadania_bez_treningu_nie_maja_tej_sekcji(web_client, open_stage):
    content = web_client.get("/zadania/").content.decode()

    assert "Zadania treningowe" not in content


def test_stage_rows_pomija_trening(edition, open_stage, training):
    rows = stage_rows(edition)

    assert [row["stage"].kind for row in rows] == [StageKind.ELIM]


def test_strona_glowna_nie_ma_treningu_ani_w_osi_czasu_ani_jako_etapu_biezacego(
    web_client, edition, open_stage, training
):
    response = web_client.get("/")
    content = response.content.decode()

    assert response.context["current_stage"] == open_stage
    assert [row["stage"].kind for row in response.context["stage_rows"]] == [StageKind.ELIM]
    assert "Zadania treningowe" not in content
    assert "2099" not in content


def test_zadania_linkuja_tresc_treningu_przez_widok_aplikacji(web_client, training):
    """Treść treningowa jest jawna od razu, ale pobiera się tak samo jak każda inna: przez widok.

    Publiczny adres storage'u nie pojawia się nigdzie – dla treningu tak samo jak dla zawodów,
    bo to ten sam ``ProblemStatementView`` i ta sama reguła ``opens_at``.
    """
    problem = Problem.objects.create(
        stage=training,
        number=1,
        title="Stan kubitu i pomiar (proste)",
        statement_pdf=SimpleUploadedFile("zadanie-1.pdf", b"%PDF-1.4 tresc", content_type="application/pdf"),
    )

    content = web_client.get("/zadania/").content.decode()

    assert f"/api/competitions/problems/{problem.pk}/statement/" in content


def test_wyniki_treningu_maja_odznake(web_client, results_page, training):
    """Ogłoszona tabela treningu wygląda jak tabela zawodów – odznaka jest jedyną różnicą."""
    training.results_published_at = timezone.now()
    training.save(update_fields=["results_published_at"])
    ResultsPublication.objects.create(
        stage=training,
        anonymization=Anonymization.CODE,
        snapshot=[{"rank": 1, "display": "OLM-TTTTTT", "district": "mazowieckie", "points": {}, "total": 12}],
    )

    content = web_client.get("/wyniki/").content.decode()

    assert "OLM-TTTTTT" in content
    assert "trening" in content

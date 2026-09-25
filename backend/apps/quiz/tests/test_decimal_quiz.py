"""Ułamki w teście online (po wydaniu 0.35.0): punkty pytań co 0,01 i wynik testu w tabeli etapu.

Model testu od początku liczył w ``Decimal`` (pytanie za 0,5 pkt, ocena częściowa wielokrotnego
wyboru), ale do tabeli wyników wynik szedł zawsze zaokrąglony do pełnych punktów – także w etapie,
w którym organizator włączył „dowolne wartości”. Sprawdzamy, że test słucha tego przełącznika,
że zaokrąglenie jest wszędzie „połówka w górę” i że edytor, import, eksport i ekrany mówią o
punktach tak samo, jak reszta systemu („0,5”, a nie „0.50”).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.services import set_scoring_scale
from apps.competitions.tests.factories import (
    CurrentEditionFactory,
    ScoringScaleFactory,
    StageEntryFactory,
)
from apps.quiz import grading, imports, services
from apps.quiz.forms import QuestionForm
from apps.quiz.tests.factories import QuizFactory, QuizStageFactory, choice_question, numeric_question
from apps.results.services import compute_stage_results

pytestmark = pytest.mark.django_db

DEFAULT_VALUES = [
    {"value": 0, "label": "brak"},
    {"value": 2, "label": "postęp"},
    {"value": 5, "label": "usterki"},
    {"value": 6, "label": "pełne"},
]


@pytest.fixture
def quiz():
    stage = QuizStageFactory(edition=CurrentEditionFactory())
    ScoringScaleFactory(stage=stage)
    return QuizFactory(stage=stage)


def make_free(stage):
    set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=CoordinatorFactory(), free_values=True)
    stage.refresh_from_db()


def solve(quiz, entry, answers: dict):
    attempt = services.start_attempt(quiz=quiz, entry=entry)
    services.save_answers(attempt=attempt, answers={str(key): value for key, value in answers.items()})
    return services.submit_attempt(attempt=attempt)


# --- zaokrąglenie: połówka w górę, do 0,01 ---------------------------------------------------------


def test_ocena_czesciowa_zaokragla_polowke_setnej_w_gore():
    """0,5 pkt × 1/4 = 0,125 → 0,13 (``ROUND_HALF_UP``), a nie 0,12 (bankierskie zaokrąglenie)."""
    verdict = grading.QuestionScore(grading.ZERO, False, Decimal(1) / Decimal(4))

    awarded = grading.award_points(
        verdict, points=Decimal("0.5"), negative_points=Decimal(0), floor=grading.FLOOR_QUESTION
    )

    assert awarded.points == Decimal("0.13")


def test_wynik_podejscia_z_oceny_czesciowej_ma_dwa_miejsca(quiz):
    """Wielokrotny wybór proporcjonalnie: 2 z 3 poprawnych wariantów za 1 pkt → 0,67."""
    question = choice_question(quiz, correct=3, total=4, settings={"partial_credit": grading.PROPORTIONAL})
    half = numeric_question(quiz, answer="1", points=Decimal("0.5"))
    entry = StageEntryFactory(stage=quiz.stage)
    correct = list(question.options.filter(is_correct=True).values_list("pk", flat=True))

    attempt = solve(quiz, entry, {question.pk: {"options": correct[:2]}, half.pk: {"value": "1"}})

    assert attempt.score == Decimal("1.17")
    assert attempt.max_points == Decimal("1.50")


# --- wynik testu w tabeli etapu: przełącznik etapu -------------------------------------------------


def test_etap_skali_zaokragla_wynik_testu_do_pelnych_punktow_jak_dotad(quiz):
    question = numeric_question(quiz, answer="1", points=Decimal("2.5"))
    entry = StageEntryFactory(stage=quiz.stage)
    solve(quiz, entry, {question.pk: {"value": "1"}})

    scores = services.stage_scores(quiz.stage)

    assert scores == {entry.pk: Decimal(3)}


def test_etap_dowolny_oddaje_wynik_testu_co_do_setnej(quiz):
    make_free(quiz.stage)
    question = numeric_question(quiz, answer="1", points=Decimal("2.5"))
    entry = StageEntryFactory(stage=quiz.stage)
    solve(quiz, entry, {question.pk: {"value": "1"}})

    scores = services.stage_scores(quiz.stage)

    assert scores == {entry.pk: Decimal("2.5")}
    assert isinstance(scores[entry.pk], Decimal)


def test_etap_dowolny_zapisuje_ulamek_w_sumie_etapu_i_w_tabeli(quiz):
    make_free(quiz.stage)
    question = numeric_question(quiz, answer="1", points=Decimal("7.25"))
    zdobywca = StageEntryFactory(stage=quiz.stage)
    pudlujacy = StageEntryFactory(stage=quiz.stage)
    solve(quiz, zdobywca, {question.pk: {"value": "1"}})
    solve(quiz, pudlujacy, {question.pk: {"value": "2"}})

    rows = compute_stage_results(quiz.stage)

    assert {row["entry_id"]: row["total"] for row in rows} == {
        zdobywca.pk: Decimal("7.25"),
        pudlujacy.pk: Decimal(0),
    }
    zdobywca.refresh_from_db()
    assert zdobywca.total_points == Decimal("7.25")


def test_etap_skali_zapisuje_w_sumie_etapu_pelne_punkty(quiz):
    question = numeric_question(quiz, answer="1", points=Decimal("7.25"))
    entry = StageEntryFactory(stage=quiz.stage)
    solve(quiz, entry, {question.pk: {"value": "1"}})

    compute_stage_results(quiz.stage)

    entry.refresh_from_db()
    assert entry.total_points == Decimal(7)


def test_etap_testu_bez_skali_liczy_jak_etap_skali():
    """Etap założony z pominięciem ``create_stage`` nie ma skali – tryb dowolny wymaga jej jawnie."""
    quiz = QuizFactory(stage=QuizStageFactory(edition=CurrentEditionFactory()))
    question = numeric_question(quiz, answer="1", points=Decimal("0.5"))
    entry = StageEntryFactory(stage=quiz.stage)
    solve(quiz, entry, {question.pk: {"value": "1"}})

    assert services.stage_scores(quiz.stage) == {entry.pk: Decimal(1)}


# --- edytor, import, eksport ----------------------------------------------------------------------


def question_data(**overrides):
    data = {
        "kind": "NUMERIC",
        "pool": "",
        "order": 1,
        "text": "Ile?",
        "points": "0,5",
        "negative_points": "0,25",
        "answer": "1",
        "tolerance_abs": "0",
        "tolerance_rel": "0",
    }
    return {**data, **overrides}


def test_edytor_przyjmuje_punkty_z_przecinkiem():
    form = QuestionForm(data=question_data())

    assert form.is_valid(), form.errors
    assert form.cleaned_data["points"] == Decimal("0.5")
    assert form.cleaned_data["negative_points"] == Decimal("0.25")


@pytest.mark.parametrize(
    ("raw", "fragment"), [("0,125", "dwa miejsca"), ("0", "mniejsze"), ("100,5", "większe")]
)
def test_edytor_odrzuca_punkty_spoza_ksztaltu_i_zakresu(raw, fragment):
    form = QuestionForm(data=question_data(points=raw, negative_points="0"))

    assert not form.is_valid()
    assert fragment in " ".join(form.errors["points"])


def test_edytor_pokazuje_zapisane_punkty_po_polsku(quiz):
    from apps.quiz.forms import initial_from_question

    question = numeric_question(quiz, points=Decimal("0.50"), negative_points=Decimal("0.25"))
    form = QuestionForm(initial=initial_from_question(question))

    html = str(form["points"]) + str(form["negative_points"])

    assert 'value="0,5"' in html and 'value="0,25"' in html


def test_import_przyjmuje_ulamek_z_przecinkiem_i_odrzuca_trzy_miejsca():
    [question] = imports.parse("## [pkt: 0,5] [ujemne: 0.25]\nPytanie\n- [x] A\n- [ ] B\n")
    assert question.points == Decimal("0.5")
    assert question.negative_points == Decimal("0.25")

    with pytest.raises(imports.ImportError_) as exc:
        imports.parse("## [pkt: 0,125]\nPytanie\n- [x] A\n- [ ] B\n")
    assert "Wiersz 1" in str(exc.value)


def test_eksport_csv_pisze_punkty_z_kropka_bez_zbednych_zer(quiz):
    whole = numeric_question(quiz, answer="1", points=Decimal("3"))
    half = numeric_question(quiz, answer="2", points=Decimal("0.5"))
    entry = StageEntryFactory(stage=quiz.stage)
    solve(quiz, entry, {whole.pk: {"value": "1"}, half.pk: {"value": "2"}})

    [row] = services.results_csv_rows(quiz)

    assert row[3:5] == ["3.5", "3.5"]

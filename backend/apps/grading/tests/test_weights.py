"""Skala przesunięta od strony oceniania: co wolno wpisać do ``Review.score`` (§ 1.2.6 b).

Ocenianie widzi skalę w postaci **przechowywanej**, bo to ono zapisuje liczby do kolumn, które
zostają nieujemne (decyzja organizatora D10). Trzy rzeczy do udowodnienia:

- bez przesunięcia (czyli w Konkursie #1) ``allowed_scores`` oddaje dokładnie ten zbiór, co przed
  etapem 2 – z etapu i z zadania, z tym samym pierwszeństwem;
- ze skalą przesuniętą wolno zapisać wartość przesuniętą i **tylko** ją: wpisanie liczby ujemnej
  wprost kończy się dzisiejszym ``SCORE_NOT_IN_SCALE``, a nie ``IntegrityError`` na kolumnie;
- skala **zadania** przesunięcia nie ma i nie dziedziczy go po etapie.
"""

from __future__ import annotations

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.services import set_scoring_scale
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.core.api import DomainError
from apps.grading.models import ReviewStatus
from apps.grading.services import allowed_scores, submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory

pytestmark = pytest.mark.django_db

FEATURE = "weighted_scoring"

NEGATIVE_VALUES = [
    {"value": -3, "label": "odpowiedź błędna"},
    {"value": 0, "label": "brak odpowiedzi"},
    {"value": 5, "label": "odpowiedź poprawna"},
]


def enable_weights(competition):
    competition.feature_flags = {**(competition.feature_flags or {}), FEATURE: True}
    competition.save(update_fields=["feature_flags"])
    return competition


def shift_scale(stage) -> None:
    """Nakłada na etap skalę z punktami ujemnymi – tą samą drogą, co koordynator z panelu."""
    enable_weights(stage.edition.competition)
    set_scoring_scale(stage, NEGATIVE_VALUES, 5, actor=CoordinatorFactory())
    stage.refresh_from_db()


def test_bez_przesuniecia_skala_etapu_jest_ta_sama_co_dzis(stage):
    assert allowed_scores(stage) == {0, 2, 5, 6}


def test_bez_przesuniecia_skala_zadania_nadal_wygrywa(stage):
    problem = ProblemFactory(
        stage=stage,
        scoring_values=[{"value": 0, "label": "nie"}, {"value": 10, "label": "tak"}],
        max_points=10,
    )

    assert allowed_scores(stage, problem) == {0, 10}


def test_skala_przesunieta_wraca_w_postaci_przechowywanej(stage):
    shift_scale(stage)

    assert allowed_scores(stage) == {0, 3, 8}


def test_skala_zadania_nie_dziedziczy_przesuniecia_etapu(stage):
    shift_scale(stage)
    problem = ProblemFactory(
        stage=stage,
        scoring_values=[{"value": 0, "label": "nie"}, {"value": 2, "label": "tak"}],
        max_points=2,
    )

    assert allowed_scores(stage, problem) == {0, 2}


def reviewed_submission(stage):
    """Praca w ocenianiu razem z przydzieloną recenzją – stan, w którym zapada ocena."""
    submission = SubmissionFactory(
        entry=StageEntryFactory(stage=stage),
        problem=ProblemFactory(stage=stage),
        status=SubmissionStatus.IN_REVIEW,
    )
    return ReviewFactory(submission=submission, status=ReviewStatus.ASSIGNED)


def test_recenzent_zapisuje_ocene_przesunieta(stage):
    """„Minus trzy” recenzenta leży w bazie jako zero – i wraca z niego przez ``StageScoring``."""
    shift_scale(stage)
    review = reviewed_submission(stage)

    saved = submit_review(review, 0)

    assert saved.score == 0


def test_punkt_ujemny_wpisany_wprost_odpada_ze_zwyklym_komunikatem(stage):
    shift_scale(stage)
    review = reviewed_submission(stage)

    with pytest.raises(DomainError) as exc:
        submit_review(review, -3)

    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"

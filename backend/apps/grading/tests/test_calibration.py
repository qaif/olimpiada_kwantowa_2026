"""Kalibracja recenzentów: odchylenia ze znakiem, rozjazdy, tendencja i sortowanie tabeli.

Przedmiotem testów jest to, co odróżnia ten ekran od zwykłego licznika recenzji: **znak**
odchylenia (surowy to co innego niż chaotyczny), zakres branych recenzji (tylko wystawiona
runda 1) i próg, poniżej którego nikogo nie podpisujemy tendencją.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.tests.factories import ProblemFactory
from apps.grading.calibration import (
    MIN_REVIEWS_FOR_TENDENCY,
    TENDENCY_LENIENT,
    TENDENCY_STRICT,
    TENDENCY_UNKNOWN,
    stage_calibration,
)
from apps.grading.models import ROUND_TIEBREAK, ReviewStatus
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def review(submission, reviewer, score, *, status=ReviewStatus.SUBMITTED, round_number=1):
    return ReviewFactory(
        submission=submission, reviewer=reviewer, score=score, status=status, round=round_number
    )


def row_for(result, reviewer):
    """Wiersz jednego recenzenta albo ``None`` – tabela ma go nie mieć, gdy nic nie wystawił."""
    return next((row for row in result["rows"] if row.reviewer.pk == reviewer.pk), None)


def test_odchylenia_maja_znak(stage):
    """Surowy dostaje wartość ujemną, łagodny dodatnią – wobec pary i wobec oceny uzgodnionej."""
    strict, lenient = ActiveReviewerFactory(), ActiveReviewerFactory()
    submission = locked_submission(stage)
    review(submission, strict, 2)
    review(submission, lenient, 6)
    FinalGradeFactory(submission=submission, score=5)

    result = stage_calibration(stage)

    assert row_for(result, strict).mean_vs_peer == pytest.approx(-4.0)
    assert row_for(result, lenient).mean_vs_peer == pytest.approx(4.0)
    assert row_for(result, strict).mean_vs_final == pytest.approx(-3.0)
    assert row_for(result, lenient).mean_vs_final == pytest.approx(1.0)


def test_rozjazd_liczy_sie_tylko_przy_dwoch_ocenach(stage):
    """Praca z jedną oceną nie ma z czym się rozjechać – nie wchodzi do mianownika."""
    first, second = ActiveReviewerFactory(), ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    disagreed = locked_submission(stage, problem=problem)
    review(disagreed, first, 2)
    review(disagreed, second, 6)
    agreed = locked_submission(stage, problem=problem)
    review(agreed, first, 5)
    review(agreed, second, 5)
    alone = locked_submission(stage, problem=problem)
    review(alone, first, 0)

    row = row_for(stage_calibration(stage), first)

    assert row.reviews == 3
    assert row.comparable == 2
    assert row.disagreements == 1
    assert row.disagreement_share == pytest.approx(0.5)


def test_szkice_anulowane_i_runda_druga_nie_wchodza(stage):
    """Do kalibracji liczy się wyłącznie wystawiona runda 1 – reszta nie jest oceną niezależną."""
    reviewer = ActiveReviewerFactory()
    other = ActiveReviewerFactory()
    draft = locked_submission(stage)
    review(draft, reviewer, 6, status=ReviewStatus.DRAFT)
    cancelled = locked_submission(stage)
    review(cancelled, reviewer, 6, status=ReviewStatus.CANCELLED)
    tiebreak = locked_submission(stage)
    review(tiebreak, other, 2)
    review(tiebreak, reviewer, 6, round_number=ROUND_TIEBREAK)

    assert row_for(stage_calibration(stage), reviewer) is None


def test_tendencja_wymaga_dosc_danych(stage):
    """Poniżej progu recenzji tabela pisze „za mało danych”, a nie nazywa kogoś surowym."""
    strict = ActiveReviewerFactory()
    peer = ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    # Ocena uzgodniona wypada pośrodku: jeden recenzent jest wobec niej surowy, drugi łagodny.
    for _ in range(MIN_REVIEWS_FOR_TENDENCY - 1):
        submission = locked_submission(stage, problem=problem)
        review(submission, strict, 0)
        review(submission, peer, 6)
        FinalGradeFactory(submission=submission, score=2)

    assert row_for(stage_calibration(stage), strict).tendency == TENDENCY_UNKNOWN

    submission = locked_submission(stage, problem=problem)
    review(submission, strict, 0)
    review(submission, peer, 6)
    FinalGradeFactory(submission=submission, score=2)

    result = stage_calibration(stage)
    assert row_for(result, strict).tendency == TENDENCY_STRICT
    assert row_for(result, peer).tendency == TENDENCY_LENIENT


def test_brak_oceny_uzgodnionej_daje_none_a_nie_zero(stage):
    """Pusta kolumna znaczy „ocenianie trwa”; zero znaczyłoby „zgodny z komisją”."""
    reviewer = ActiveReviewerFactory()
    submission = locked_submission(stage)
    review(submission, reviewer, 2)

    row = row_for(stage_calibration(stage), reviewer)

    assert row.mean_vs_final is None
    assert row.tendency == TENDENCY_UNKNOWN


def test_sortowanie_po_parametrze_adresu(stage):
    """Parametr ``sort`` układa tabelę; minus odwraca kierunek, a nieznana nazwa spada do domyślnej."""
    few, many = ActiveReviewerFactory(), ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    review(locked_submission(stage, problem=problem), few, 2)
    for _ in range(3):
        review(locked_submission(stage, problem=problem), many, 2)

    ascending = stage_calibration(stage, sort="reviews")
    descending = stage_calibration(stage, sort="-reviews")
    unknown = stage_calibration(stage, sort="cokolwiek")

    assert [row.reviewer.pk for row in ascending["rows"]] == [few.pk, many.pk]
    assert [row.reviewer.pk for row in descending["rows"]] == [many.pk, few.pk]
    assert unknown["sort"] == "reviews"
    assert unknown["descending"] is True


def test_puste_odchylenia_sortuja_sie_na_koniec(stage):
    """Wiersz bez odchylenia jest nieznany, a nie najlepszy – nie może wypłynąć na górę."""
    graded, ungraded = ActiveReviewerFactory(), ActiveReviewerFactory()
    problem = ProblemFactory(stage=stage)
    with_grade = locked_submission(stage, problem=problem)
    review(with_grade, graded, 6)
    FinalGradeFactory(submission=with_grade, score=6)
    review(locked_submission(stage, problem=problem), ungraded, 6)

    rows = stage_calibration(stage, sort="vs_final")["rows"]

    assert rows[-1].reviewer.pk == ungraded.pk

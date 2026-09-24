"""Oceny dziesiętne w ocenianiu (wydanie 0.35.0): każda droga zapisu przez jedną regułę.

W etapie z dowolnymi wartościami (``ScoringScale.free_values``) recenzja, poprawka, korekta
koordynatora, rozstrzygnięcie rozjazdu i API przyjmują 4,25 – a w etapie „tylko ze skali” dalej nie.
Zgodność ocen rundy 1 jest równością liczb dziesiętnych (4,25 = 4,25, a 4,25 ≠ 4,26); średniej nie ma.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.accounts.tests.factories import CoordinatorFactory
from apps.competitions.services import set_scoring_scale
from apps.competitions.tests.factories import ProblemFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import FinalGrade, GradeMethod, Review, ReviewStatus
from apps.grading.services import (
    override_final_grade,
    resolve_moderation,
    save_draft,
    set_review_score,
    submit_review,
)
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db

REVIEWS_URL = "/api/grading/reviews/"
DEFAULT_VALUES = [
    {"value": 0, "label": "brak"},
    {"value": 2, "label": "postęp"},
    {"value": 5, "label": "usterki"},
    {"value": 6, "label": "pełne"},
]


def make_free(stage):
    set_scoring_scale(stage, DEFAULT_VALUES, 6, actor=CoordinatorFactory(), free_values=True)
    stage.refresh_from_db()


def in_review(stage, problem=None):
    submission = locked_submission(stage, problem=problem)
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    return submission


def two_reviews(stage, problem=None):
    submission = in_review(stage, problem)
    return submission, ReviewFactory(submission=submission), ReviewFactory(submission=submission)


def test_recenzja_z_ocena_ulamkowa_w_trybie_dowolnym(stage):
    make_free(stage)
    submission, first, _second = two_reviews(stage)

    saved = submit_review(first, "4,25")

    first.refresh_from_db()
    assert first.score == Decimal("4.25")
    assert saved.score == Decimal("4.25")
    entry = AuditLog.objects.filter(action="review.submitted").latest("id")
    assert entry.diff["score"] == 4.25


def test_zgodne_oceny_ulamkowe_daja_konsensus(stage):
    make_free(stage)
    submission, first, second = two_reviews(stage)

    submit_review(first, "4,25")
    submit_review(second, Decimal("4.25"))

    grade = FinalGrade.objects.get(submission=submission)
    assert grade.score == Decimal("4.25")
    assert grade.method == GradeMethod.CONSENSUS


def test_oceny_rozne_o_setna_ida_do_moderacji(stage):
    make_free(stage)
    submission, first, second = two_reviews(stage)

    submit_review(first, "4,25")
    submit_review(second, "4,26")

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.MODERATION
    assert not FinalGrade.objects.filter(submission=submission).exists()


def test_tryb_skali_nadal_odrzuca_ulamek(stage):
    _submission, first, _second = two_reviews(stage)

    with pytest.raises(DomainError) as exc:
        submit_review(first, "4,5")

    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"
    first.refresh_from_db()
    assert first.status == ReviewStatus.ASSIGNED


def test_trzecie_miejsce_po_przecinku_odrzucone_a_nie_zaokraglone(stage):
    make_free(stage)
    _submission, first, _second = two_reviews(stage)

    with pytest.raises(DomainError) as exc:
        submit_review(first, "4,255")

    assert exc.value.machine_code == "SCORE_INVALID"


def test_szkic_zapisuje_ulamek_bez_sprawdzania_skali_ale_nie_smieci(stage):
    _submission, first, _second = two_reviews(stage)

    save_draft(first, score="4,5")
    first.refresh_from_db()
    assert first.score == Decimal("4.50")

    with pytest.raises(DomainError) as exc:
        save_draft(first, score="4,555")
    assert exc.value.machine_code == "SCORE_INVALID"


def test_koordynator_rozstrzyga_rozjazd_ocena_ulamkowa(stage):
    make_free(stage)
    submission, first, second = two_reviews(stage)
    submit_review(first, 4)
    submit_review(second, 5)

    resolve_moderation(submission, CoordinatorFactory(), "4,5", None, "po naradzie")

    assert FinalGrade.objects.get(submission=submission).score == Decimal("4.50")


def test_korekta_koordynatora_i_punkty_recenzji_ulamkowe(stage):
    make_free(stage)
    submission, first, second = two_reviews(stage)
    submit_review(first, 5)
    submit_review(second, 5)
    coordinator = CoordinatorFactory()

    set_review_score(Review.objects.get(pk=first.pk), "5,75", actor=coordinator)
    result = override_final_grade(
        submission, "5,5", rationale="Korekta po ponownym przeczytaniu.", actor=coordinator
    )

    assert Review.objects.get(pk=first.pk).score == Decimal("5.75")
    assert result["grade"].score == Decimal("5.50")
    with pytest.raises(DomainError):
        override_final_grade(
            submission, "6,5", rationale="Korekta po ponownym przeczytaniu.", actor=coordinator
        )


def test_zadanie_z_samym_maksimum_przyjmuje_ocene_ponad_skale_etapu(stage):
    make_free(stage)
    problem = ProblemFactory(stage=stage, max_points=Decimal("12.5"))
    _submission, first, _second = two_reviews(stage, problem)

    submit_review(first, "11,75")

    first.refresh_from_db()
    assert first.score == Decimal("11.75")


def test_rubryka_w_trybie_dowolnym_sprawdza_zakres_a_nie_liste(stage):
    from apps.grading.rubric import criteria_for, set_criteria

    make_free(stage)
    problem = ProblemFactory(stage=stage)
    set_criteria(problem, [{"max_points": 2, "title": "a"}, {"max_points": 2, "title": "b"}], actor=None)
    criteria = list(criteria_for(problem))
    _submission, first, _second = two_reviews(stage, problem)

    submit_review(
        first,
        0,
        rubric=[
            {"criterion_id": criteria[0].pk, "points": 2, "comment": ""},
            {"criterion_id": criteria[1].pk, "points": 1, "comment": ""},
        ],
    )

    first.refresh_from_db()
    assert first.score == Decimal(3)


# --- API ------------------------------------------------------------------------------------------


def test_api_przyjmuje_tekst_z_przecinkiem_i_oddaje_liczbe_json(client, stage):
    make_free(stage)
    _submission, first, _second = two_reviews(stage)
    client.force_authenticate(first.reviewer.user)

    response = client.post(f"{REVIEWS_URL}{first.pk}/submit/", {"score": "4,25"}, format="json")

    assert response.status_code == 200, response.content
    assert response.json()["score"] == 4.25
    listed = client.get(REVIEWS_URL).json()
    assert listed[0]["score"] == 4.25


def test_api_etapu_skali_oddaje_ocene_calkowita_jako_int(client, stage):
    _submission, first, _second = two_reviews(stage)
    client.force_authenticate(first.reviewer.user)

    response = client.post(f"{REVIEWS_URL}{first.pk}/submit/", {"score": 5}, format="json")

    assert response.status_code == 200
    assert response.content.count(b'"score":5,') + response.content.count(b'"score":5}') == 1


def test_api_odmowy_z_kodami(client, stage):
    make_free(stage)
    _submission, first, _second = two_reviews(stage)
    client.force_authenticate(first.reviewer.user)
    url = f"{REVIEWS_URL}{first.pk}/submit/"

    too_precise = client.post(url, {"score": 4.255}, format="json")
    out_of_range = client.post(url, {"score": 6.5}, format="json")

    assert too_precise.status_code == 400
    assert out_of_range.status_code == 400
    assert out_of_range.json()["code"] == "SCORE_NOT_IN_SCALE"

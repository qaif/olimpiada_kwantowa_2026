"""Kryteria 5, 6, 7 T-05: skala punktowa, konsensus/rozjazd, runda rozjemcza i moderacja."""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.competitions.tests.factories import ScoringScaleFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import ROUND_TIEBREAK, FinalGrade, GradeMethod, Review, ReviewStatus
from apps.grading.services import (
    assign_third_reviewer,
    resolve_moderation,
    submit_review,
)
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def under_review(stage, **kwargs):
    submission = locked_submission(stage, **kwargs)
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    return submission


def two_reviews(stage, **kwargs):
    submission = under_review(stage, **kwargs)
    return submission, ReviewFactory(submission=submission), ReviewFactory(submission=submission)


def test_score_outside_scale_is_rejected(stage):
    """5. Ocena 3 przy skali {0, 2, 5, 6} → 400 SCORE_NOT_IN_SCALE."""
    review = ReviewFactory(submission=under_review(stage))

    with pytest.raises(DomainError) as exc:
        submit_review(review, 3, "", "", [])

    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"
    assert exc.value.status_code == 400
    review.refresh_from_db()
    assert review.status == ReviewStatus.ASSIGNED
    assert review.score is None


def test_two_equal_scores_give_consensus_final_grade(stage):
    """6a. Zgodne oceny → FinalGrade(CONSENSUS) i GRADED_PROVISIONAL."""
    submission, first, second = two_reviews(stage)

    submit_review(first, 5, "wewnętrznie", "dla uczestnika", [])
    submit_review(second, 5, "wewnętrznie", "dla uczestnika", [])

    submission.refresh_from_db()
    grade = FinalGrade.objects.get(submission=submission)
    assert grade.score == 5
    assert grade.method == GradeMethod.CONSENSUS
    # Konsensus wynika z reguły, nie z decyzji człowieka.
    assert grade.decided_by is None
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_two_different_scores_send_submission_to_moderation(stage):
    """6b. Różne oceny → MODERATION i brak FinalGrade."""
    submission, first, second = two_reviews(stage)

    submit_review(first, 5, "", "", [])
    submit_review(second, 2, "", "", [])

    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.MODERATION
    assert not FinalGrade.objects.filter(submission=submission).exists()


def test_third_reviewer_resolves_with_third_review_method(stage):
    """7a. Trzeci recenzent (runda 2) → FinalGrade(THIRD_REVIEW)."""
    submission, first, second = two_reviews(stage)
    submit_review(first, 5, "", "", [])
    submit_review(second, 2, "", "", [])
    submission.refresh_from_db()
    third = ActiveReviewerFactory()

    tiebreak = assign_third_reviewer(submission, third)
    assert tiebreak.round == ROUND_TIEBREAK
    assert tiebreak.status == ReviewStatus.ASSIGNED

    submit_review(tiebreak, 5, "rozjemczo", "", [])

    submission.refresh_from_db()
    grade = FinalGrade.objects.get(submission=submission)
    assert grade.method == GradeMethod.THIRD_REVIEW
    assert grade.score == 5
    assert grade.decided_by == third.user
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL


def test_coordinator_resolves_with_moderation_and_audit_entry(stage):
    """7b. Koordynator → FinalGrade(MODERATION) + wpis AuditLog."""
    submission, first, second = two_reviews(stage)
    submit_review(first, 6, "", "", [])
    submit_review(second, 2, "", "", [])
    submission.refresh_from_db()
    coordinator = CoordinatorFactory()

    grade = resolve_moderation(submission, coordinator, 5, None, "Posiedzenie komisji 2026-03-01")

    submission.refresh_from_db()
    assert grade.method == GradeMethod.MODERATION
    assert grade.score == 5
    assert grade.decided_by == coordinator
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL
    entry = AuditLog.objects.get(action="grade.decided", target_id=str(grade.pk))
    assert entry.actor == coordinator
    assert entry.diff == {"submission_id": submission.pk, "score": 5, "method": GradeMethod.MODERATION}


def test_reviewer_from_round_one_cannot_become_third_reviewer(stage):
    """Konflikt interesów: rozjazdu nie rozstrzyga autor oceny z rundy 1."""
    submission, first, second = two_reviews(stage)
    submit_review(first, 6, "", "", [])
    submit_review(second, 2, "", "", [])
    submission.refresh_from_db()

    with pytest.raises(DomainError) as exc:
        assign_third_reviewer(submission, first.reviewer)

    assert exc.value.machine_code == "REVIEWER_CONFLICT_OF_INTEREST"


def test_random_reviewer_cannot_resolve_moderation(stage):
    """Rozjazd rozstrzyga koordynator albo wyznaczony trzeci recenzent – nikt inny."""
    submission, first, second = two_reviews(stage)
    submit_review(first, 6, "", "", [])
    submit_review(second, 2, "", "", [])
    submission.refresh_from_db()
    outsider = ActiveReviewerFactory()

    with pytest.raises(DomainError) as exc:
        resolve_moderation(submission, outsider.user, 5)

    assert exc.value.machine_code == "NOT_ALLOWED_TO_RESOLVE"
    assert exc.value.status_code == 403
    assert not FinalGrade.objects.filter(submission=submission).exists()


def test_resolve_requires_moderation_state(stage):
    """Zgodne oceny zamykają sprawę – koordynator nie „poprawia” konsensusu tą ścieżką."""
    submission, first, second = two_reviews(stage)
    submit_review(first, 5, "", "", [])
    submit_review(second, 5, "", "", [])
    submission.refresh_from_db()

    with pytest.raises(DomainError) as exc:
        resolve_moderation(submission, CoordinatorFactory(), 6)

    assert exc.value.machine_code == "NOT_IN_MODERATION"


def test_review_cannot_be_submitted_for_graded_submission(stage):
    """Zgłoszenie poza IN_REVIEW/MODERATION nie przyjmuje ocen (maszyna stanów 2.4)."""
    submission = locked_submission(stage)
    review = ReviewFactory(submission=submission)

    with pytest.raises(DomainError) as exc:
        submit_review(review, 5, "", "", [])

    assert exc.value.machine_code == "SUBMISSION_NOT_REVIEWABLE"
    assert exc.value.status_code == 409


def test_custom_scale_is_respected(stage):
    """Skala jest parametrem etapu – walidacja czyta ``ScoringScale.allowed_values()``."""
    stage.scoring_scale.delete()
    stage.refresh_from_db()
    ScoringScaleFactory(
        stage=stage,
        values=[{"value": 0, "label": "zero"}, {"value": 10, "label": "komplet"}],
        max_value=10,
    )
    review = ReviewFactory(submission=under_review(stage))

    with pytest.raises(DomainError) as exc:
        submit_review(review, 5, "", "", [])
    assert exc.value.machine_code == "SCORE_NOT_IN_SCALE"

    submit_review(review, 10, "", "", [])
    review.refresh_from_db()
    assert review.score == 10


def test_third_reviewer_endpoint_flow(client, stage):
    """Ścieżka API moderacji: koordynator wyznacza trzeciego, ten wystawia ocenę rozjemczą."""
    submission, first, second = two_reviews(stage)
    submit_review(first, 6, "", "", [])
    submit_review(second, 2, "", "", [])
    third = ActiveReviewerFactory()

    client.force_authenticate(CoordinatorFactory())
    assigned = client.post(
        f"/api/grading/moderation/{submission.pk}/assign-third/",
        {"reviewer_id": third.pk},
        format="json",
    )
    assert assigned.status_code == 200
    assert assigned.data["round"] == ROUND_TIEBREAK

    review = Review.objects.get(submission=submission, round=ROUND_TIEBREAK)
    client.force_authenticate(third.user)
    resolved = client.post(f"/api/grading/reviews/{review.pk}/submit/", {"score": 6}, format="json")

    assert resolved.status_code == 200
    submission.refresh_from_db()
    assert submission.status == SubmissionStatus.GRADED_PROVISIONAL
    assert FinalGrade.objects.get(submission=submission).method == GradeMethod.THIRD_REVIEW

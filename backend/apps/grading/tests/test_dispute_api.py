"""``GET /api/grading/reviews/{id}/dispute/`` – materiał rozjemczy rundy 2 (T-08).

Kontrakt: trzeci recenzent widzi punkty i komentarze wewnętrzne obu ocen rundy 1, ale **nie**
tożsamość ich autorów. Recenzja rundy 1 nie ma takiego materiału i dostaje 404 (nie 403), żeby
odpowiedź nie potwierdzała istnienia zasobu.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory, CoordinatorFactory
from apps.grading.models import ROUND_TIEBREAK, ReviewStatus
from apps.grading.tests.conftest import locked_submission
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus

pytestmark = pytest.mark.django_db


def _moderated_submission(stage):
    submission = locked_submission(stage)
    submission.status = SubmissionStatus.MODERATION
    submission.save(update_fields=["status"])
    return submission


def _round_one_pair(submission):
    return (
        ReviewFactory(
            submission=submission,
            reviewer=ActiveReviewerFactory(),
            status=ReviewStatus.SUBMITTED,
            score=2,
            comment_internal="istotny postęp, ale luka w dowodzie",
        ),
        ReviewFactory(
            submission=submission,
            reviewer=ActiveReviewerFactory(),
            status=ReviewStatus.SUBMITTED,
            score=6,
            comment_internal="dowód pełny",
        ),
    )


def test_third_reviewer_sees_both_round_one_scores_without_identities(client, stage):
    submission = _moderated_submission(stage)
    first, second = _round_one_pair(submission)
    third = ActiveReviewerFactory()
    tiebreak = ReviewFactory(submission=submission, reviewer=third, round=ROUND_TIEBREAK)
    client.force_authenticate(third.user)

    response = client.get(f"/api/grading/reviews/{tiebreak.pk}/dispute/")

    assert response.status_code == 200
    assert response.data == [
        {"score": 2, "comment_internal": "istotny postęp, ale luka w dowodzie"},
        {"score": 6, "comment_internal": "dowód pełny"},
    ]
    payload = str(response.data)
    for review in (first, second):
        assert review.reviewer.user.email not in payload
        assert str(review.reviewer_id) not in [str(row.get("reviewer_id")) for row in response.data]


def test_round_one_review_gets_404(client, stage):
    submission = _moderated_submission(stage)
    first, _ = _round_one_pair(submission)
    client.force_authenticate(first.reviewer.user)

    response = client.get(f"/api/grading/reviews/{first.pk}/dispute/")

    assert response.status_code == 404
    assert response.data["code"] == "NOT_A_TIEBREAK_REVIEW"


def test_other_reviewer_gets_404_for_foreign_tiebreak(client, stage):
    submission = _moderated_submission(stage)
    _round_one_pair(submission)
    tiebreak = ReviewFactory(submission=submission, reviewer=ActiveReviewerFactory(), round=ROUND_TIEBREAK)
    client.force_authenticate(ActiveReviewerFactory().user)

    assert client.get(f"/api/grading/reviews/{tiebreak.pk}/dispute/").status_code == 404


def test_coordinator_without_reviewer_role_is_forbidden(client, stage):
    submission = _moderated_submission(stage)
    _round_one_pair(submission)
    tiebreak = ReviewFactory(submission=submission, reviewer=ActiveReviewerFactory(), round=ROUND_TIEBREAK)
    client.force_authenticate(CoordinatorFactory())

    assert client.get(f"/api/grading/reviews/{tiebreak.pk}/dispute/").status_code == 403

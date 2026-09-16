"""Nowa wersja rozwiązania unieważnia ocenę rozpoczętą na wersji wcześniejszej.

Druga połowa oceniania przed zamknięciem etapu: skoro komitet czyta prace przy otwartym oknie
uploadu, uczestnik musi móc wysłać poprawkę – a wtedy dotychczasowa ocena przestaje obowiązywać.
Testy idą przez prawdziwy upload (``create_submission``), bo to jest droga, którą reguła działa.
"""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.tests.factories import ProblemFactory, StageEntryFactory
from apps.core.api import DomainError
from apps.core.models import AuditLog
from apps.grading.models import FinalGrade, GradeMethod, Review, ReviewCancelReason, ReviewStatus
from apps.grading.services import (
    _assignable_submissions,
    assign_reviewer_to_submission,
    is_assignable,
    reviews_for_reviewer,
)
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.results.models import ResultsPublication
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.services import create_submission, lock_submission_for_review
from apps.submissions.tests.factories import SubmissionFactory, pdf_upload

pytestmark = pytest.mark.django_db


@pytest.fixture
def problem(stage):
    return ProblemFactory(stage=stage, number=1, allowed_formats=["pdf"], max_file_mb=1)


@pytest.fixture
def entry(stage):
    return StageEntryFactory(stage=stage)


@pytest.fixture
def graded(entry, problem):
    """Pierwsza wersja pracy w ocenie: dwie recenzje rundy 1 i ocena uzgodniona."""
    submission = SubmissionFactory(
        entry=entry, problem=problem, version=1, status=SubmissionStatus.GRADED_PROVISIONAL
    )
    reviews = [
        ReviewFactory(
            submission=submission,
            reviewer=ActiveReviewerFactory(),
            status=ReviewStatus.SUBMITTED,
            score=5,
        )
        for _ in range(2)
    ]
    grade = FinalGradeFactory(submission=submission, score=5, method=GradeMethod.CONSENSUS)
    return submission, reviews, grade


def upload_new_version(entry, problem):
    return create_submission(
        user=entry.participant.user,
        stage=entry.stage,
        problem_number=problem.number,
        upload=pdf_upload(),
    )


def test_new_version_cancels_reviews_with_superseded_reason(entry, problem, graded):
    old, reviews, _ = graded

    upload_new_version(entry, problem)

    for review in reviews:
        review.refresh_from_db()
        assert review.status == ReviewStatus.CANCELLED
        assert review.cancel_reason == ReviewCancelReason.SUPERSEDED
        # Punkty zostają jako historia – anulowana jest ważność oceny, a nie zapis o niej.
        assert review.score == 5


def test_new_version_deletes_final_grade_and_audits_it(entry, problem, graded):
    old, _, _ = graded

    upload_new_version(entry, problem)

    assert not FinalGrade.objects.filter(submission=old).exists()
    withdrawn = AuditLog.objects.get(action="grade.withdrawn")
    assert withdrawn.diff["reason"] == "SUPERSEDED"
    assert withdrawn.diff["score"] == 5
    assert withdrawn.actor == entry.participant.user


def test_old_version_goes_back_to_submitted_with_audit(entry, problem, graded):
    old, _, _ = graded

    new = upload_new_version(entry, problem)

    old.refresh_from_db()
    assert old.status == SubmissionStatus.SUBMITTED
    superseded = AuditLog.objects.get(action="submission.superseded")
    assert superseded.diff == {
        "old_id": old.pk,
        "new_id": new.pk,
        "cancelled_reviews": 2,
        "grade_withdrawn": True,
    }


def test_new_version_is_the_one_that_enters_grading(entry, problem, graded, stage):
    """Po unieważnieniu do oceniania wchodzi wyłącznie nowa wersja – stara zostaje historią."""
    old, _, _ = graded

    new = upload_new_version(entry, problem)
    lock_submission_for_review(new)

    old.refresh_from_db()
    new.refresh_from_db()
    assert [item.pk for item in _assignable_submissions(stage)] == [new.pk]
    assert is_assignable(new) and not is_assignable(old)
    assign_reviewer_to_submission(new, ActiveReviewerFactory())
    new.refresh_from_db()
    assert new.status == SubmissionStatus.IN_REVIEW


def test_reviewer_queue_no_longer_offers_the_old_version(entry, problem, graded):
    """Recenzent ma zobaczyć powód, a nie zniknięcie zadania bez śladu."""
    _, reviews, _ = graded
    reviewer = reviews[0].reviewer

    upload_new_version(entry, problem)

    queue = list(reviews_for_reviewer(reviewer))
    assert [item.status for item in queue] == [ReviewStatus.CANCELLED]
    assert queue[0].cancel_reason == ReviewCancelReason.SUPERSEDED


def test_earlier_versions_without_grading_are_left_alone(entry, problem):
    """Zwykła poprawka przed rozpoczęciem oceniania nie generuje ani jednego wpisu audytowego."""
    old = SubmissionFactory(entry=entry, problem=problem, version=1)

    upload_new_version(entry, problem)

    old.refresh_from_db()
    assert old.status == SubmissionStatus.SUBMITTED
    assert not AuditLog.objects.filter(action="submission.superseded").exists()


@pytest.mark.parametrize("status", [SubmissionStatus.FINAL, SubmissionStatus.APPEALED])
def test_finalised_version_refuses_the_upload(entry, problem, status):
    """Bezpiecznik: oceny, którą uczestnik i komisja już znają, nie da się cicho unieważnić."""
    SubmissionFactory(entry=entry, problem=problem, version=1, status=status)

    with pytest.raises(DomainError) as exc:
        upload_new_version(entry, problem)

    assert exc.value.machine_code == "SUBMISSION_FINALISED"
    assert exc.value.status_code == 409
    # Transakcja wycofana w całości – nowa wersja nie powstała nawet jako wiersz.
    assert Submission.objects.filter(entry=entry, problem=problem).count() == 1


def test_published_results_refuse_the_upload(entry, problem, graded, stage):
    ResultsPublication.objects.create(stage=stage)

    with pytest.raises(DomainError) as exc:
        upload_new_version(entry, problem)

    assert exc.value.machine_code == "SUBMISSION_FINALISED"
    assert Review.objects.filter(status=ReviewStatus.CANCELLED).count() == 0

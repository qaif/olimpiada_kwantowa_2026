"""Raport postępu oceniania (``apps.grading.reports``) – warstwa danych, bez ekranu.

Testy ekranu leżą w ``apps/web/tests/test_coordinator_progress.py``. Tutaj sprawdzamy same reguły
liczenia: rozłączność segmentów paska, definicję zaległości i to, że przypomnienie zostawia
w audycie wyłącznie liczby.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.core.models import AuditLog
from apps.grading.models import ReviewStatus
from apps.grading.reports import (
    FALLBACK_OVERDUE_DAYS,
    remind_reviewers,
    reviewer_rows,
    stage_progress,
)
from apps.grading.tests.factories import FinalGradeFactory, ReviewFactory
from apps.submissions.models import Submission, SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def _overdue(review):
    """Cofa termin recenzji tak, żeby była zaległa w obu definicjach raportu.

    Oba pola naraz (``assigned_at`` i – gdy model je ma – ``due_at``), bo raport wybiera definicję
    w czasie działania. Test ma sprawdzać „zaległa jest widoczna”, a nie to, który wariant
    akurat obowiązuje.
    """
    past = timezone.now() - timedelta(days=FALLBACK_OVERDUE_DAYS + 1)
    fields = {"assigned_at": past}
    if any(field.name == "due_at" for field in review._meta.get_fields()):
        fields["due_at"] = past
    type(review).objects.filter(pk=review.pk).update(**fields)
    return review


def test_segments_are_disjoint_and_cover_every_submission(stage):
    graded = locked_submission(stage)
    Submission.objects.filter(pk=graded.pk).update(status=SubmissionStatus.FINAL)
    FinalGradeFactory(submission=graded)
    locked_submission(stage)

    progress = stage_progress(stage)
    counts = {segment["key"]: segment["count"] for segment in progress["segments"]}

    assert progress["total"] == 2
    assert counts["graded"] == 1
    assert counts["locked"] == 1
    assert sum(counts.values()) == progress["total"]


def test_final_grade_is_counted_separately_from_the_state(stage):
    submission = locked_submission(stage)
    Submission.objects.filter(pk=submission.pk).update(status=SubmissionStatus.APPEALED)
    FinalGradeFactory(submission=submission)

    progress = stage_progress(stage)
    counts = {segment["key"]: segment["count"] for segment in progress["segments"]}

    # Praca po reklamacji ma ocenę, ale nie jest w segmencie „ocenione” – to dwie różne informacje.
    assert counts["graded"] == 0
    assert counts["appealed"] == 1
    assert progress["with_final_grade"] == 1


def test_shares_fill_the_whole_bar(stage):
    for _ in range(3):
        locked_submission(stage)

    progress = stage_progress(stage)

    assert sum(segment["share"] for segment in progress["segments"]) == 100


def test_empty_stage_has_zero_shares(stage):
    progress = stage_progress(stage)

    assert progress["total"] == 0
    assert {segment["share"] for segment in progress["segments"]} == {0}


def test_reviewer_without_any_assignment_still_has_a_row(stage):
    reviewer = ActiveReviewerFactory()

    rows = reviewer_rows(stage)

    assert [row["member"].pk for row in rows] == [reviewer.pk]
    assert rows[0]["assigned"] == 0


def test_old_unfinished_assignment_counts_as_overdue(stage):
    reviewer = ActiveReviewerFactory()
    _overdue(ReviewFactory(submission=locked_submission(stage), reviewer=reviewer))

    assert reviewer_rows(stage)[0]["overdue"] == 1


def test_submitted_review_is_never_overdue(stage):
    reviewer = ActiveReviewerFactory()
    _overdue(
        ReviewFactory(
            submission=locked_submission(stage),
            reviewer=reviewer,
            status=ReviewStatus.SUBMITTED,
            score=5,
            submitted_at=timezone.now(),
        )
    )

    row = reviewer_rows(stage)[0]
    assert row["submitted"] == 1
    assert row["overdue"] == 0


def test_cancelled_review_is_neither_pending_nor_overdue(stage):
    reviewer = ActiveReviewerFactory()
    _overdue(
        ReviewFactory(submission=locked_submission(stage), reviewer=reviewer, status=ReviewStatus.CANCELLED)
    )

    row = reviewer_rows(stage)[0]
    assert row["assigned"] == 0
    assert row["overdue"] == 0


def test_reminder_reaches_only_reviewers_with_overdue_work(stage, django_capture_on_commit_callbacks):
    late = ActiveReviewerFactory()
    on_time = ActiveReviewerFactory()
    _overdue(ReviewFactory(submission=locked_submission(stage), reviewer=late))
    ReviewFactory(submission=locked_submission(stage), reviewer=on_time)
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        summary = remind_reviewers(stage, only_overdue=True)

    assert summary["reviewers"] == 1
    assert [message.to for message in mail.outbox] == [[late.user.email]]


def test_reminder_mail_carries_no_participant_data(stage, django_capture_on_commit_callbacks):
    reviewer = ActiveReviewerFactory()
    submission = _overdue(ReviewFactory(submission=locked_submission(stage), reviewer=reviewer)).submission
    mail.outbox.clear()

    with django_capture_on_commit_callbacks(execute=True):
        remind_reviewers(stage, member=reviewer)

    body = mail.outbox[0].body
    # Ocenianie jest ślepe także w poczcie: ani pseudonimu pracy, ani nazwiska uczestnika.
    assert submission.entry.participant.public_code not in body
    assert submission.entry.participant.user.last_name not in body


def test_audit_entry_has_counters_only(stage, django_capture_on_commit_callbacks):
    reviewer = ActiveReviewerFactory()
    _overdue(ReviewFactory(submission=locked_submission(stage), reviewer=reviewer))

    with django_capture_on_commit_callbacks(execute=True):
        remind_reviewers(stage, member=reviewer)

    log = AuditLog.objects.get(action="reviewer.reminded")
    assert log.diff == {
        "stage_id": stage.pk,
        "reviewers": 1,
        "reviews": 1,
        "overdue": 1,
        "scope": "one",
    }

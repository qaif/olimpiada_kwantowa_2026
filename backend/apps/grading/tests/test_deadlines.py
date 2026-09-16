"""Terminy pojedynczych recenzji i przypomnienia o nich.

Reguła terminu (``apps.grading.deadlines.review_due_at``): chwila przydziału + dni z etapu,
z sufitem w deadline recenzji etapu, który jednak nigdy nie cofa terminu w przeszłość.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.core.models import AuditLog
from apps.grading.deadlines import is_overdue, review_due_at, reviews_needing_reminder
from apps.grading.models import Review, ReviewStatus
from apps.grading.services import assign_reviewers
from apps.grading.tasks import remind_overdue_reviews
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


@pytest.fixture
def submission(stage):
    created = locked_submission(stage)
    created.status = SubmissionStatus.IN_REVIEW
    created.save(update_fields=["status"])
    return created


def test_due_date_is_counted_from_the_assignment(stage):
    now = timezone.now()
    stage.review_deadline_days = 7
    stage.save(update_fields=["review_deadline_days"])

    assert review_due_at(stage, now) == now + timedelta(days=7)


def shift_timeline(stage, *, opens, deadline, review, appeal_opens, appeal_closes, days):
    """Przesuwa oś czasu etapu (dni względem „teraz”) razem z liczbą dni na recenzję.

    ``update()`` zamiast ``save()``: kolejność dat pilnują constrainty w bazie, więc terminy trzeba
    przestawić **wszystkie naraz** – ustawienie samego deadline'u recenzji na przeszłość odbiłoby
    się od ``competitions_stage_deadline_before_review``.
    """
    from apps.competitions.models import Stage

    now = timezone.now()
    Stage.objects.filter(pk=stage.pk).update(
        opens_at=now + timedelta(days=opens),
        deadline_at=now + timedelta(days=deadline),
        review_deadline_at=now + timedelta(days=review),
        appeal_window_opens_at=now + timedelta(days=appeal_opens),
        appeal_window_closes_at=now + timedelta(days=appeal_closes),
        review_deadline_days=days,
    )
    stage.refresh_from_db()
    return now


def test_stage_review_deadline_is_the_ceiling(stage):
    """Termin osobisty nie może wypaść po terminie recenzji całego etapu."""
    now = shift_timeline(stage, opens=-30, deadline=-10, review=3, appeal_opens=4, appeal_closes=11, days=30)

    assert review_due_at(stage, now) == stage.review_deadline_at


def test_ceiling_never_moves_the_due_date_into_the_past(stage):
    """Praca przydzielona po deadline etapu dostaje pełne okno – inaczej rodzi się po terminie."""
    now = shift_timeline(stage, opens=-40, deadline=-20, review=-1, appeal_opens=1, appeal_closes=8, days=5)

    assert review_due_at(stage, now) == now + timedelta(days=5)


def test_assign_reviewers_stamps_the_same_due_date_on_the_whole_wave(stage, submission):
    ActiveReviewerFactory()
    ActiveReviewerFactory()
    stage.review_deadline_days = 10
    stage.save(update_fields=["review_deadline_days"])

    result = assign_reviewers(stage, per_submission=2)

    due_dates = set(Review.objects.values_list("due_at", flat=True))
    assert result["assignments"] == 2
    assert len(due_dates) == 1
    assert due_dates.pop() == result["due_at"]


def test_overdue_marks_only_open_reviews(submission):
    yesterday = timezone.now() - timedelta(days=1)
    open_review = ReviewFactory(submission=submission, due_at=yesterday)
    done = ReviewFactory(submission=submission, due_at=yesterday, status=ReviewStatus.SUBMITTED, score=2)

    assert is_overdue(open_review) is True
    assert is_overdue(done) is False


def test_reminder_covers_overdue_and_soon_but_not_distant_reviews(submission, stage):
    soon = ReviewFactory(submission=submission, due_at=timezone.now() + timedelta(days=1))
    late = ReviewFactory(submission=submission, due_at=timezone.now() - timedelta(days=3))
    distant = ReviewFactory(submission=submission, due_at=timezone.now() + timedelta(days=9))

    selected = {review.pk for review in reviews_needing_reminder()}

    assert selected == {soon.pk, late.pk}
    assert distant.pk not in selected


def test_reminder_sends_one_mail_per_reviewer_and_not_twice_a_day(submission):
    reviewer = ActiveReviewerFactory()
    other = locked_submission(submission.entry.stage)
    other.status = SubmissionStatus.IN_REVIEW
    other.save(update_fields=["status"])
    first = ReviewFactory(submission=submission, reviewer=reviewer, due_at=timezone.now() - timedelta(days=1))
    second = ReviewFactory(submission=other, reviewer=reviewer, due_at=timezone.now() - timedelta(days=2))

    result = remind_overdue_reviews()

    assert result == {"reviewers": 1, "reviews": 2}
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [reviewer.user.email]
    assert "po terminie" in message.subject
    for review in (first, second):
        review.refresh_from_db()
        assert review.reminded_at is not None
        assert review.submission.entry.participant.public_code in message.body
    # Audyt liczy listy, nie adresy ani treści.
    entry = AuditLog.objects.filter(action="review.reminder_sent").get()
    assert entry.diff == {"reviews": 2}

    assert remind_overdue_reviews() == {"reviewers": 0, "reviews": 0}
    assert len(mail.outbox) == 1


def test_reminder_skips_reviews_of_submissions_that_left_grading(submission):
    ReviewFactory(submission=submission, due_at=timezone.now() - timedelta(days=1))
    submission.status = SubmissionStatus.FINAL
    submission.save(update_fields=["status"])

    assert remind_overdue_reviews() == {"reviewers": 0, "reviews": 0}
    assert mail.outbox == []

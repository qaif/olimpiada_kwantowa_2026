"""Seria prac jednego zadania: „poprzednia / następna” i licznik w kolejce recenzenta."""

import pytest

from apps.accounts.tests.factories import ActiveReviewerFactory
from apps.competitions.tests.factories import ProblemFactory
from apps.grading.models import ReviewStatus
from apps.grading.navigation import group_by_problem, queue_position
from apps.grading.services import reviews_for_reviewer
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import SubmissionStatus

from .conftest import locked_submission

pytestmark = pytest.mark.django_db


def in_review(stage, problem):
    submission = locked_submission(stage, problem=problem)
    submission.status = SubmissionStatus.IN_REVIEW
    submission.save(update_fields=["status"])
    return submission


@pytest.fixture
def reviewer():
    return ActiveReviewerFactory()


@pytest.fixture
def problem(stage):
    return ProblemFactory(stage=stage, number=1)


@pytest.fixture
def queue(stage, problem, reviewer):
    """Trzy własne prace tego samego zadania, przydzielone w znanej kolejności."""
    return [ReviewFactory(submission=in_review(stage, problem), reviewer=reviewer) for _ in range(3)]


def test_position_and_neighbours_follow_the_assignment_order(queue):
    first, second, third = queue

    assert queue_position(first) == {
        "previous": None,
        "next": second.pk,
        "position": 1,
        "total": 3,
    }
    assert queue_position(second)["previous"] == first.pk
    assert queue_position(third) == {
        "previous": second.pk,
        "next": None,
        "position": 3,
        "total": 3,
    }


def test_series_covers_one_problem_only(stage, queue, reviewer):
    other_problem = ProblemFactory(stage=stage, number=2)
    ReviewFactory(submission=in_review(stage, other_problem), reviewer=reviewer)

    assert queue_position(queue[0])["total"] == 3


def test_series_covers_one_reviewer_only(stage, problem, queue):
    stranger = ActiveReviewerFactory()
    ReviewFactory(submission=in_review(stage, problem), reviewer=stranger)

    assert queue_position(queue[0])["total"] == 3


def test_submitted_review_stays_in_its_own_series(queue):
    """Po wysłaniu oceny recenzent wraca na tę stronę – i dalej ma widzieć, co jest następne."""
    first = queue[0]
    first.status = ReviewStatus.SUBMITTED
    first.score = 2
    first.save(update_fields=["status", "score"])

    position = queue_position(first)

    assert position["position"] == 1
    assert position["next"] == queue[1].pk


def test_cancelled_reviews_drop_out_of_the_series(queue):
    queue[1].status = ReviewStatus.CANCELLED
    queue[1].save(update_fields=["status"])

    position = queue_position(queue[0])

    assert position["total"] == 2
    assert position["next"] == queue[2].pk


def test_grouping_counts_open_reviews_per_problem(stage, problem, queue, reviewer):
    other_problem = ProblemFactory(stage=stage, number=2)
    ReviewFactory(submission=in_review(stage, other_problem), reviewer=reviewer)
    queue[0].status = ReviewStatus.SUBMITTED
    queue[0].score = 2
    queue[0].save(update_fields=["status", "score"])

    groups = group_by_problem(list(reviews_for_reviewer(reviewer)))

    assert [group["problem"].number for group in groups] == [1, 2]
    assert [group["open"] for group in groups] == [2, 1]
    assert [len(group["rows"]) for group in groups] == [3, 1]

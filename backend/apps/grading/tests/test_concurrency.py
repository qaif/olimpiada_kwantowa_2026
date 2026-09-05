"""Wyścig o ``FinalGrade``: dwie równoczesne oceny nie mogą utworzyć dwóch ocen uzgodnionych.

Test wymaga prawdziwej bazy z commitami (``transaction=True``) – ``select_for_update`` na
``Submission`` ma sens tylko wtedy, gdy transakcje faktycznie się kończą i widzą swoje zapisy.
Wzorzec (Barrier + wątki + ``connection.close()``) jest ten sam co w
``apps/submissions/tests/test_concurrency.py``.
"""

import threading

import pytest
from django.db import connection

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    ProblemFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.models import FinalGrade, GradeMethod, Review
from apps.grading.services import submit_review
from apps.grading.tests.factories import ReviewFactory
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory


@pytest.mark.django_db(transaction=True)
def test_two_parallel_submits_create_exactly_one_final_grade():
    stage = StageFactory(kind=StageKind.ELIM)
    ScoringScaleFactory(stage=stage)
    entry = StageEntryFactory(stage=stage, participant=ParticipantFactory())
    submission = SubmissionFactory(
        entry=entry, problem=ProblemFactory(stage=stage), status=SubmissionStatus.IN_REVIEW
    )
    reviews = [ReviewFactory(submission=submission), ReviewFactory(submission=submission)]

    barrier = threading.Barrier(2, timeout=15)
    errors: list = []

    def worker(review: Review) -> None:
        try:
            barrier.wait()
            submit_review(review, 5, "", "", [])
        except Exception as exc:  # zbieramy, żeby test pokazał przyczynę, nie tylko timeout
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(review,)) for review in reviews]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    grades = list(FinalGrade.objects.filter(submission=submission))
    assert len(grades) == 1
    assert grades[0].method == GradeMethod.CONSENSUS
    assert grades[0].score == 5
    assert Submission.objects.get(pk=submission.pk).status == SubmissionStatus.GRADED_PROVISIONAL

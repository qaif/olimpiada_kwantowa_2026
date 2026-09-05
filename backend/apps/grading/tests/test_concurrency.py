"""Wyścig o ``FinalGrade``: dwie równoczesne oceny nie mogą utworzyć dwóch ocen uzgodnionych.

Test wymaga prawdziwej bazy z commitami (``transaction=True``) – ``select_for_update`` na
``Submission`` ma sens tylko wtedy, gdy transakcje faktycznie się kończą i widzą swoje zapisy.
Wzorzec (Barrier + wątki + ``connection.close()``) jest ten sam co w
``apps/submissions/tests/test_concurrency.py``.
"""

import threading

import pytest
from django.db import connection

from apps.accounts.tests.factories import ActiveReviewerFactory, ParticipantFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    ProblemFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.models import ROUND_BLIND, FinalGrade, GradeMethod, Review
from apps.grading.services import assign_reviewers, submit_review
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


@pytest.mark.django_db(transaction=True)
def test_two_parallel_assignments_do_not_double_assign_reviewers():
    """Przegląd Critica T-05, finding 4: równoległy przydział dla tego samego etapu.

    Bez blokady doradczej oba wywołania czytają ten sam obraz świata: każde widzi rozwiązanie bez
    recenzji i przydziela mu własną parę recenzentów. Wynikiem jest czterech recenzentów zamiast
    dwóch albo ``IntegrityError`` (500) na unikalności przydziału. Poprawnie: jedno wywołanie
    przydziela, drugie widzi komplet i nie robi nic.
    """
    stage = StageFactory(kind=StageKind.ELIM)
    ScoringScaleFactory(stage=stage)
    for _ in range(3):
        ActiveReviewerFactory()
    entry = StageEntryFactory(stage=stage, participant=ParticipantFactory())
    submission = SubmissionFactory(
        entry=entry, problem=ProblemFactory(stage=stage), status=SubmissionStatus.LOCKED
    )

    barrier = threading.Barrier(2, timeout=15)
    errors: list = []

    def worker() -> None:
        try:
            barrier.wait()
            assign_reviewers(stage)
        except Exception as exc:  # zbieramy, żeby test pokazał przyczynę, nie tylko timeout
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    reviewers = set(
        Review.objects.filter(submission=submission, round=ROUND_BLIND).values_list("reviewer_id", flat=True)
    )
    assert len(reviewers) == 2
    assert Review.objects.filter(submission=submission).count() == 2
    assert Submission.objects.get(pk=submission.pk).status == SubmissionStatus.IN_REVIEW

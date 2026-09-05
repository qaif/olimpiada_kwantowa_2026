"""Wyścig o ``AppealDecision``: dwie równoczesne decyzje nie mogą rozstrzygnąć reklamacji dwa razy.

Test wymaga prawdziwej bazy z commitami (``transaction=True``) – ``select_for_update`` na
``Submission`` ma sens tylko wtedy, gdy transakcje faktycznie się kończą i widzą swoje zapisy.
Wzorzec (Barrier + wątki + ``connection.close()``) jest ten sam co w
``apps/grading/tests/test_concurrency.py``.
"""

import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.accounts.tests.factories import ParticipantFactory
from apps.appeals.models import Appeal, AppealDecision, AppealStatus
from apps.appeals.services import decide_appeal, file_appeal
from apps.appeals.tests.factories import VALID_ARGUMENT, AppealsCommitteeMemberFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    ProblemFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.models import FinalGrade, GradeMethod
from apps.grading.tests.factories import FinalGradeFactory
from apps.submissions.models import Submission, SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory


@pytest.mark.django_db(transaction=True)
def test_two_parallel_decisions_create_exactly_one_appeal_decision():
    now = timezone.now()
    stage = StageFactory(
        kind=StageKind.ELIM,
        opens_at=now - timedelta(days=40),
        deadline_at=now - timedelta(days=30),
        review_deadline_at=now - timedelta(days=2),
        appeal_window_opens_at=now - timedelta(hours=1),
        appeal_window_closes_at=now + timedelta(days=7),
    )
    ScoringScaleFactory(stage=stage)
    participant = ParticipantFactory()
    entry = StageEntryFactory(stage=stage, participant=participant)
    submission = SubmissionFactory(
        entry=entry,
        problem=ProblemFactory(stage=stage),
        status=SubmissionStatus.GRADED_PROVISIONAL,
    )
    FinalGradeFactory(submission=submission, score=2)
    appeal = file_appeal(participant.user, submission, VALID_ARGUMENT)
    members = [AppealsCommitteeMemberFactory(), AppealsCommitteeMemberFactory()]

    barrier = threading.Barrier(2, timeout=15)
    errors: list = []

    def worker(member) -> None:
        try:
            barrier.wait()
            decide_appeal(appeal, member, AppealStatus.ACCEPTED, 6, "Rozwiązanie jest poprawne.")
        except Exception as exc:  # zbieramy, żeby test pokazał przyczynę, nie tylko timeout
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(member,)) for member in members]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    # Dokładnie jedna decyzja przechodzi; druga odbija się o czytelne 409, a nie o błąd bazy.
    assert len(errors) == 1, errors
    assert getattr(errors[0], "machine_code", None) == "APPEAL_ALREADY_DECIDED"
    assert AppealDecision.objects.filter(appeal=appeal).count() == 1
    assert Appeal.objects.get(pk=appeal.pk).status == AppealStatus.ACCEPTED
    grade = FinalGrade.objects.get(submission=submission)
    assert (grade.score, grade.method) == (6, GradeMethod.APPEAL)
    assert Submission.objects.get(pk=submission.pk).status == SubmissionStatus.FINAL

"""Wspólne narzędzia testów wyników: etap ze skalą i progiem oraz sfinalizowane zgłoszenia."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import QualificationMode, StageEntryStatus, StageKind
from apps.competitions.tests.factories import (
    EditionFactory,
    ProblemFactory,
    QualificationRuleFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.grading.tests.factories import FinalGradeFactory
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory


@pytest.fixture
def client() -> APIClient:
    return APIClient()


def make_stage(
    *,
    kind=StageKind.ELIM,
    edition=None,
    problems: int = 2,
    mode=QualificationMode.MIN_POINTS,
    min_points=0,
    top_n=None,
):
    """Etap ze skalą 0/2/5/6, progiem kwalifikacji i ``problems`` zadaniami o numerach 1..n."""
    stage = StageFactory(kind=kind, edition=edition or EditionFactory())
    ScoringScaleFactory(stage=stage)
    QualificationRuleFactory(stage=stage, mode=mode, min_points=min_points, top_n=top_n)
    for number in range(1, problems + 1):
        ProblemFactory(stage=stage, number=number, title=f"Zadanie {number}")
    return stage


def stage_problems(stage) -> list:
    return list(stage.problems.order_by("number"))


def graded_entry(
    stage,
    scores,
    *,
    participant=None,
    status=StageEntryStatus.REGISTERED,
    problems=None,
    **participant_kwargs,
):
    """Wpis do etapu z kompletem zgłoszeń FINAL i ocen uzgodnionych.

    ``scores`` odpowiada kolejnym zadaniom etapu; ``None`` oznacza brak zgłoszenia (0 punktów).
    """
    participant = participant or ParticipantFactory(**participant_kwargs)
    entry = StageEntryFactory(stage=stage, participant=participant, status=status)
    for problem, score in zip(problems or stage_problems(stage), scores, strict=False):
        if score is None:
            continue
        submission = SubmissionFactory(entry=entry, problem=problem, status=SubmissionStatus.FINAL)
        FinalGradeFactory(submission=submission, score=score)
    return entry

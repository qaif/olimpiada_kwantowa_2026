"""Wspólne narzędzia testów wyników: etap ze skalą i progiem oraz sfinalizowane zgłoszenia."""

from datetime import timedelta

import pytest
from django.utils import timezone
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


#: Etap „domknięty”: otwarty 90 dni temu, więc jego okno reklamacji (deadline + 14 + 2 dni, przez
#: 7 dni) zamknęło się dawno temu. Progi i publikacja wolno liczyć dopiero wtedy (PROJEKT.md 2.4),
#: więc to jest domyślny etap testów wyników.
CLOSED_STAGE_AGE = timedelta(days=90)


def make_stage(
    *,
    kind=StageKind.ELIM,
    edition=None,
    problems: int = 2,
    mode=QualificationMode.MIN_POINTS,
    min_points=0,
    top_n=None,
    appeals_open: bool = False,
):
    """Etap ze skalą 0/2/5/6, progiem kwalifikacji i ``problems`` zadaniami o numerach 1..n.

    ``appeals_open=True`` daje etap z **otwartym** oknem reklamacji – do testów bramy czasowej.
    """
    opens_at = timezone.now() - (timedelta(days=1) if appeals_open else CLOSED_STAGE_AGE)
    stage = StageFactory(kind=kind, edition=edition or EditionFactory(), opens_at=opens_at)
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

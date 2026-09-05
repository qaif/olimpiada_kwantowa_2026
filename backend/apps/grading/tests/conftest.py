"""Wspólne narzędzia testów oceniania: etap ze skalą i zablokowane rozwiązania gotowe do przydziału."""

import pytest
from rest_framework.test import APIClient

from apps.accounts.tests.factories import ParticipantFactory
from apps.competitions.models import StageKind
from apps.competitions.tests.factories import (
    ProblemFactory,
    ScoringScaleFactory,
    StageEntryFactory,
    StageFactory,
)
from apps.submissions.models import SubmissionStatus
from apps.submissions.tests.factories import SubmissionFactory


@pytest.fixture
def client() -> APIClient:
    return APIClient()


@pytest.fixture
def stage():
    """Etap eliminacyjny z domyślną skalą 0/2/5/6."""
    created = StageFactory(kind=StageKind.ELIM)
    ScoringScaleFactory(stage=created)
    return created


@pytest.fixture
def district_stage():
    """Etap okręgowy – tam obowiązuje reguła konfliktu interesów."""
    created = StageFactory(kind=StageKind.DISTRICT)
    ScoringScaleFactory(stage=created)
    return created


def locked_submission(stage, *, district="mazowiecki", problem=None, **participant_kwargs):
    """Rozwiązanie w stanie LOCKED – dokładnie taki stan zostawia ``close_stage``."""
    participant = ParticipantFactory(district=district, **participant_kwargs)
    entry = StageEntryFactory(stage=stage, participant=participant)
    return SubmissionFactory(
        entry=entry,
        problem=problem or ProblemFactory(stage=stage),
        status=SubmissionStatus.LOCKED,
    )
